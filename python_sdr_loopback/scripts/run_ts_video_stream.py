from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


TS_PACKET_SIZE = 188


def run_command(command: list[str]) -> int:
    print(f"command={' '.join(command)}")
    return subprocess.run(command).returncode


def ts_aligned_batch_bytes(value: int) -> int:
    return max(TS_PACKET_SIZE, (value // TS_PACKET_SIZE) * TS_PACKET_SIZE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert video to MPEG-TS, send it over SDR, and rebuild live TS output.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/ts_stream"))
    parser.add_argument("--ts-input-file", type=Path, help="Converted TS path. Defaults to <work-dir>/<input-stem>.ts.")
    parser.add_argument("--recovered-ts", type=Path, help="Recovered SDR TS path. Defaults to <work-dir>/recovered.ts.")
    parser.add_argument("--live-ts", type=Path, help="Watcher rebuilt TS path. Defaults to <work-dir>/live.ts.")
    parser.add_argument("--segment-dir", type=Path, help="Defaults to <work-dir>/segments.")
    parser.add_argument("--manifest-file", type=Path, help="Defaults to <segment-dir>/manifest.json.")
    parser.add_argument("--batch-bytes", type=int, default=120_000)
    parser.add_argument(
        "--no-ts-packet-align",
        action="store_true",
        help="Do not round --batch-bytes down to an MPEG-TS 188-byte packet boundary.",
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--skip-convert", action="store_true", help="Treat --input-file as an existing MPEG-TS file.")
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
    parser.add_argument("--watch-timeout-sec", type=float, default=30.0)
    args = parser.parse_args()

    if args.batch_bytes <= 0:
        print("--batch-bytes must be positive", file=sys.stderr)
        return 2
    if args.inter_batch_sec < 0.0:
        print("--inter-batch-sec must be non-negative", file=sys.stderr)
        return 2
    if args.watch_timeout_sec < 0.0:
        print("--watch-timeout-sec must be non-negative", file=sys.stderr)
        return 2
    if not args.input_file.exists():
        print(f"input_exists=false")
        print(f"input_file={args.input_file}")
        return 2
    effective_batch_bytes = args.batch_bytes if args.no_ts_packet_align else ts_aligned_batch_bytes(args.batch_bytes)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    ts_input = args.ts_input_file or args.work_dir / f"{args.input_file.stem}.ts"
    recovered_ts = args.recovered_ts or args.work_dir / "recovered.ts"
    live_ts = args.live_ts or args.work_dir / "live.ts"
    segment_dir = args.segment_dir or args.work_dir / "segments"
    manifest_file = args.manifest_file or segment_dir / "manifest.json"

    print(f"ts_video_stream=true")
    print(f"source_input_file={args.input_file}")
    print(f"ts_input_file={ts_input}")
    print(f"recovered_ts={recovered_ts}")
    print(f"live_ts={live_ts}")
    print(f"segment_dir={segment_dir}")
    print(f"manifest_file={manifest_file}")
    print("playable_hint=true")
    print(f"playable_file={live_ts}")
    print("playable_format=mpegts")
    print(f"ts_packet_size={TS_PACKET_SIZE}")
    print(f"batch_bytes_requested={args.batch_bytes}")
    print(f"batch_bytes_effective={effective_batch_bytes}")
    print(f"ts_packet_aligned={str(effective_batch_bytes % TS_PACKET_SIZE == 0).lower()}")

    if args.skip_convert or args.input_file.suffix.lower() == ".ts":
        if args.input_file.resolve() != ts_input.resolve():
            ts_input.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.input_file, ts_input)
        print("ts_convert_skipped=true")
    else:
        ffmpeg_path = shutil.which(args.ffmpeg)
        if ffmpeg_path is None:
            print("ffmpeg_found=false")
            print("Install ffmpeg, add it to PATH, pass --ffmpeg with an absolute path, or use --skip-convert for .ts input.")
            return 3
        convert_command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(args.input_file),
            "-c",
            "copy",
            "-f",
            "mpegts",
            str(ts_input),
        ]
        print("ts_convert_skipped=false")
        if run_command(convert_command) != 0:
            print("ts_convert_ok=false")
            return 3
        print("ts_convert_ok=true")

    video_stream_script = Path(__file__).with_name("run_video_segment_stream.py")
    send_command = [
        sys.executable,
        str(video_stream_script),
        "--input-file",
        str(ts_input),
        "--output-file",
        str(recovered_ts),
        "--segment-dir",
        str(segment_dir),
        "--manifest-file",
        str(manifest_file),
        "--batch-bytes",
        str(effective_batch_bytes),
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
    if run_command(send_command) != 0:
        print("sdr_video_stream_ok=false")
        return 4
    print("sdr_video_stream_ok=true")

    watcher_script = Path(__file__).with_name("watch_video_segments.py")
    watch_command = [
        sys.executable,
        str(watcher_script),
        "--segment-dir",
        str(segment_dir),
        "--manifest-file",
        str(manifest_file),
        "--output-file",
        str(live_ts),
        "--timeout-sec",
        str(args.watch_timeout_sec),
    ]
    if run_command(watch_command) != 0:
        print("ts_watch_ok=false")
        return 5
    print("ts_watch_ok=true")
    print(f"ts_stream_ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
