from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VideoPreset:
    name: str
    video_bitrate: str
    video_bufsize: str
    chunk_bytes: int
    description: str


PRESETS: tuple[VideoPreset, ...] = (
    VideoPreset("eband_stable", "300k", "600k", 16_000, "300k stable E-band demo"),
    VideoPreset("eband_quality", "400k", "800k", 20_000, "400k quality E-band demo"),
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def numeric_values(records: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
        elif isinstance(value, str):
            try:
                values.append(float(value))
            except ValueError:
                pass
    return values


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def parse_key_values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def int_value(values: dict[str, str], key: str) -> int:
    try:
        return int(float(values.get(key, "0")))
    except ValueError:
        return 0


def float_value(values: dict[str, str], key: str) -> float:
    try:
        return float(values.get(key, "0"))
    except ValueError:
        return 0.0


def summarize_metrics(path: Path) -> dict[str, Any]:
    records = load_jsonl(path)
    failed = [record for record in records if not bool(record.get("chunk_ok"))]
    capture_attempts = numeric_values(records, "capture_attempts")
    context_recreates = numeric_values(records, "context_recreates_this_batch")
    chunk_elapsed = numeric_values(records, "chunk_elapsed_sec")
    capture_elapsed = numeric_values(records, "sdr_capture_elapsed_sec")
    decode_elapsed = numeric_values(records, "decode_elapsed_sec")
    goodput = numeric_values(records, "stream_goodput_bps")
    return {
        "chunk_count": len(records),
        "chunk_failed_count": len(failed),
        "capture_attempts_total": int(sum(capture_attempts)),
        "capture_attempts_avg": statistics.mean(capture_attempts) if capture_attempts else 0.0,
        "context_recreates_total": int(sum(context_recreates)),
        "chunk_elapsed_p50_sec": statistics.median(chunk_elapsed) if chunk_elapsed else 0.0,
        "chunk_elapsed_p90_sec": percentile(chunk_elapsed, 0.90),
        "sdr_capture_p50_sec": statistics.median(capture_elapsed) if capture_elapsed else 0.0,
        "sdr_capture_p90_sec": percentile(capture_elapsed, 0.90),
        "decode_p50_sec": statistics.median(decode_elapsed) if decode_elapsed else 0.0,
        "stream_goodput_p50_bps": statistics.median(goodput) if goodput else 0.0,
        "stream_goodput_p90_bps": percentile(goodput, 0.90),
        "metrics_ok": bool(records) and not failed,
    }


def preset_by_name(name: str) -> VideoPreset:
    for preset in PRESETS:
        if preset.name == name:
            return preset
    raise ValueError(f"unknown preset: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark E-band low-latency video demo presets.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/eband_video_benchmark"))
    parser.add_argument("--presets", default="all", help="Comma-separated preset names, or all.")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-chunks", type=int, default=0, help="0 means stream until ffmpeg EOF.")
    parser.add_argument("--open-player", action="store_true", help="Open ffplay during benchmark runs.")
    parser.add_argument("--quiet-run-output", action="store_true")
    parser.add_argument("--encoder-preset", default="veryfast")
    parser.add_argument("--scale-height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--gop", type=int, default=12)
    parser.add_argument("--tx-settle-sec", type=float, default=0.02)
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--sample-rate", type=int, default=30_000_000)
    parser.add_argument("--symbol-rate", type=int, default=7_500_000)
    parser.add_argument("--bandwidth", type=int, default=20_000_000)
    parser.add_argument("--tx-gain-db", type=float, default=-12.0)
    parser.add_argument("--rx-gain-db", type=float, default=10.0)
    parser.add_argument("--tx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--rx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--tx-port", default="B")
    parser.add_argument("--rx-port", default="B_BALANCED")
    args = parser.parse_args()

    if args.runs <= 0:
        print("--runs must be positive", file=sys.stderr)
        return 2
    if not args.input_file.exists():
        print("input_exists=false")
        print(f"input_file={args.input_file}")
        return 2

    selected = PRESETS if args.presets == "all" else tuple(preset_by_name(name.strip()) for name in args.presets.split(","))
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    stream_script = Path(__file__).with_name("run_low_latency_ts_stream.py")
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()

    for preset in selected:
        for run_index in range(1, args.runs + 1):
            work_dir = args.artifact_root / f"{preset.name}_run{run_index:03d}"
            command = [
                sys.executable,
                str(stream_script),
                "--input-file",
                str(args.input_file),
                "--work-dir",
                str(work_dir),
                "--video-bitrate",
                preset.video_bitrate,
                "--video-bufsize",
                preset.video_bufsize,
                "--encoder-preset",
                args.encoder_preset,
                "--scale-height",
                str(args.scale_height),
                "--fps",
                str(args.fps),
                "--gop",
                str(args.gop),
                "--chunk-bytes",
                str(preset.chunk_bytes),
                "--tx-settle-sec",
                str(args.tx_settle_sec),
                "--uri",
                args.uri,
                "--lo-hz",
                str(args.lo_hz),
                "--sample-rate",
                str(args.sample_rate),
                "--symbol-rate",
                str(args.symbol_rate),
                "--bandwidth",
                str(args.bandwidth),
                "--tx-gain-db",
                str(args.tx_gain_db),
                "--rx-gain-db",
                str(args.rx_gain_db),
                "--tx-channel",
                str(args.tx_channel),
                "--rx-channel",
                str(args.rx_channel),
                "--tx-port",
                args.tx_port,
                "--rx-port",
                args.rx_port,
            ]
            if args.max_chunks:
                command.extend(["--max-chunks", str(args.max_chunks)])
            if not args.open_player:
                command.append("--no-player")

            print(f"benchmark_preset={preset.name}")
            print(f"benchmark_run={run_index}")
            print(f"benchmark_work_dir={work_dir}")
            run_started = time.perf_counter()
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            wall_elapsed = time.perf_counter() - run_started
            if not args.quiet_run_output:
                print(result.stdout, end="")

            values = parse_key_values(result.stdout)
            metrics = summarize_metrics(work_dir / "metrics.jsonl")
            run_ok = result.returncode == 0 and values.get("low_latency_stream_ok") == "true" and bool(metrics["metrics_ok"])
            row: dict[str, Any] = {
                "preset": preset.name,
                "description": preset.description,
                "run": run_index,
                "video_bitrate": preset.video_bitrate,
                "video_bufsize": preset.video_bufsize,
                "chunk_bytes": preset.chunk_bytes,
                "tx_settle_sec": args.tx_settle_sec,
                "wall_elapsed_sec": wall_elapsed,
                "return_code": result.returncode,
                "run_ok": run_ok,
                "stream_chunks_ok": int_value(values, "stream_chunks_ok"),
                "stream_chunks_failed": int_value(values, "stream_chunks_failed"),
                "stream_output_bytes": int_value(values, "stream_output_bytes"),
                "stream_total_elapsed_sec": float_value(values, "stream_total_elapsed_sec"),
                "stream_goodput_bps": float_value(values, "stream_goodput_bps"),
                **metrics,
            }
            rows.append(row)
            print(f"benchmark_run_ok={str(run_ok).lower()}")
            print(f"benchmark_wall_elapsed_sec={wall_elapsed:.3f}")
            print(f"benchmark_chunk_count={row['chunk_count']}")
            print(f"benchmark_chunk_failed_count={row['chunk_failed_count']}")
            print(f"benchmark_context_recreates_total={row['context_recreates_total']}")
            print(f"benchmark_capture_attempts_avg={row['capture_attempts_avg']:.3f}")
            print(f"benchmark_sdr_capture_p50_sec={row['sdr_capture_p50_sec']:.3f}")
            print(f"benchmark_stream_goodput_p50_bps={row['stream_goodput_p50_bps']:.0f}")

    csv_file = args.artifact_root / "summary.csv"
    json_file = args.artifact_root / "summary.json"
    if rows:
        with csv_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        json_file.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    total_elapsed = time.perf_counter() - started
    ok_rows = [row for row in rows if bool(row["run_ok"])]
    print(f"benchmark_presets={len(selected)}")
    print(f"benchmark_runs={len(rows)}")
    print(f"benchmark_successful_runs={len(ok_rows)}")
    print(f"benchmark_total_elapsed_sec={total_elapsed:.3f}")
    print(f"benchmark_summary_csv={csv_file}")
    print(f"benchmark_summary_json={json_file}")
    print(f"benchmark_ok={str(len(rows) > 0 and len(ok_rows) == len(rows)).lower()}")
    return 0 if rows and len(ok_rows) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
