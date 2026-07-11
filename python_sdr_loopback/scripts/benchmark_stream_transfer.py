from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path


def parse_key_values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def float_value(values: dict[str, str], key: str) -> float:
    try:
        return float(values.get(key, "0"))
    except ValueError:
        return 0.0


def int_value(values: dict[str, str], key: str) -> int:
    try:
        return int(float(values.get(key, "0")))
    except ValueError:
        return 0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Repeat stream file transfers and summarize E310 loopback stability.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--artifact-prefix", type=Path, default=Path("artifacts/stream_benchmark"))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--batch-bytes", type=int, default=160_000)
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--sample-rate", type=int, default=30_000_000)
    parser.add_argument("--symbol-rate", type=int, default=7_500_000)
    parser.add_argument("--bandwidth", type=int, default=20_000_000)
    parser.add_argument("--tx-gain-db", type=float, default=-12.0)
    parser.add_argument("--tx-amplitude", type=float, default=0.5)
    parser.add_argument("--tx-dac-scale", type=float, default=8192.0)
    parser.add_argument("--rx-gain-db", type=float, default=10.0)
    parser.add_argument("--tx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--rx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--tx-port", default="B")
    parser.add_argument("--rx-port", default="B_BALANCED")
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--file-guard-packets", type=int, default=0)
    parser.add_argument("--tx-cyclic-copies", type=int, default=3)
    parser.add_argument("--rx-discard-buffers", type=int, default=0)
    parser.add_argument("--inter-batch-sec", type=float, default=0.25)
    parser.add_argument("--min-rx-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--rx-level-retries", type=int, default=8)
    parser.add_argument("--no-progressive", action="store_true", help="Disable progressive output writes.")
    parser.add_argument("--quiet-run-output", action="store_true", help="Do not echo full child script output.")
    args = parser.parse_args()

    if args.runs <= 0:
        print("--runs must be positive", file=sys.stderr)
        return 2
    if args.batch_bytes <= 0:
        print("--batch-bytes must be positive", file=sys.stderr)
        return 2
    if args.inter_batch_sec < 0.0:
        print("--inter-batch-sec must be non-negative", file=sys.stderr)
        return 2

    script = Path(__file__).with_name("run_file_transfer_stream.py")
    args.artifact_prefix.parent.mkdir(parents=True, exist_ok=True)

    elapsed_values: list[float] = []
    goodput_values: list[float] = []
    context_recreates_values: list[int] = []
    capture_attempt_avg_values: list[float] = []
    capture_attempt_total_values: list[int] = []
    successful_runs = 0
    benchmark_start = time.perf_counter()

    for run_index in range(1, args.runs + 1):
        output_file = args.artifact_prefix.with_name(f"{args.artifact_prefix.name}_run{run_index:03d}.bin")
        command = [
            sys.executable,
            str(script),
            "--input-file",
            str(args.input_file),
            "--output-file",
            str(output_file),
            "--batch-bytes",
            str(args.batch_bytes),
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
            "--tx-amplitude",
            str(args.tx_amplitude),
            "--tx-dac-scale",
            str(args.tx_dac_scale),
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
            "--payload-bytes",
            str(args.payload_bytes),
            "--file-guard-packets",
            str(args.file_guard_packets),
            "--tx-cyclic-copies",
            str(args.tx_cyclic_copies),
            "--rx-discard-buffers",
            str(args.rx_discard_buffers),
            "--inter-batch-sec",
            str(args.inter_batch_sec),
            "--min-rx-rms-dbfs",
            str(args.min_rx_rms_dbfs),
            "--rx-level-retries",
            str(args.rx_level_retries),
        ]
        if not args.no_progressive:
            command.append("--write-progressive")

        print(f"benchmark_run={run_index}")
        run_start = time.perf_counter()
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        wall_elapsed = time.perf_counter() - run_start
        if not args.quiet_run_output:
            print(result.stdout, end="")

        values = parse_key_values(result.stdout)
        run_ok = result.returncode == 0 and values.get("file_ok") == "true"
        elapsed = float_value(values, "total_elapsed_sec")
        goodput = float_value(values, "file_goodput_bps")
        context_recreates = int_value(values, "profile_context_recreates")
        capture_attempts_total = int_value(values, "profile_capture_attempts_total")
        capture_attempts_avg = float_value(values, "profile_capture_attempts_avg")
        print(f"benchmark_run_ok={str(run_ok).lower()}")
        print(f"benchmark_run_wall_elapsed_sec={wall_elapsed:.3f}")
        print(f"benchmark_run_total_elapsed_sec={elapsed:.3f}")
        print(f"benchmark_run_goodput_bps={goodput:.0f}")
        print(f"benchmark_run_context_recreates={context_recreates}")
        print(f"benchmark_run_capture_attempts_avg={capture_attempts_avg:.3f}")

        if run_ok:
            successful_runs += 1
            elapsed_values.append(elapsed)
            goodput_values.append(goodput)
            context_recreates_values.append(context_recreates)
            capture_attempt_total_values.append(capture_attempts_total)
            capture_attempt_avg_values.append(capture_attempts_avg)

    total_wall_elapsed = time.perf_counter() - benchmark_start
    success_rate = successful_runs / args.runs
    print(f"benchmark_runs={args.runs}")
    print(f"benchmark_successful_runs={successful_runs}")
    print(f"benchmark_success_rate={success_rate:.6f}")
    print(f"benchmark_total_wall_elapsed_sec={total_wall_elapsed:.3f}")
    if successful_runs:
        print(f"elapsed_avg_sec={statistics.mean(elapsed_values):.3f}")
        print(f"elapsed_min_sec={min(elapsed_values):.3f}")
        print(f"elapsed_p50_sec={statistics.median(elapsed_values):.3f}")
        print(f"elapsed_p90_sec={percentile(elapsed_values, 0.90):.3f}")
        print(f"elapsed_max_sec={max(elapsed_values):.3f}")
        print(f"goodput_avg_bps={statistics.mean(goodput_values):.0f}")
        print(f"goodput_min_bps={min(goodput_values):.0f}")
        print(f"goodput_max_bps={max(goodput_values):.0f}")
        print(f"context_recreates_avg={statistics.mean(context_recreates_values):.3f}")
        print(f"context_recreates_max={max(context_recreates_values)}")
        print(f"capture_attempts_total_avg={statistics.mean(capture_attempt_total_values):.3f}")
        print(f"capture_attempts_avg_avg={statistics.mean(capture_attempt_avg_values):.3f}")
    print(f"benchmark_ok={str(successful_runs == args.runs).lower()}")
    return 0 if successful_runs == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
