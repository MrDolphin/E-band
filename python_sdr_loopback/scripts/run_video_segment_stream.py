from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a video file as recovered SDR stream segments.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, default=Path("artifacts/video_stream_recovered.mp4"))
    parser.add_argument("--segment-dir", type=Path, default=Path("artifacts/video_segments"))
    parser.add_argument("--manifest-file", type=Path, help="Defaults to <segment-dir>/manifest.json.")
    parser.add_argument("--metrics-file", type=Path, help="Defaults to <segment-dir>/metrics.jsonl.")
    parser.add_argument("--batch-bytes", type=int, default=120_000)
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
    parser.add_argument("--rx-frame-copies", type=float, default=1.0)
    parser.add_argument("--min-rx-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--rx-level-retries", type=int, default=8)
    args = parser.parse_args()

    if args.batch_bytes <= 0:
        print("--batch-bytes must be positive", file=sys.stderr)
        return 2
    if args.inter_batch_sec < 0.0:
        print("--inter-batch-sec must be non-negative", file=sys.stderr)
        return 2
    if args.rx_frame_copies <= 0.0:
        print("--rx-frame-copies must be positive", file=sys.stderr)
        return 2

    script = Path(__file__).with_name("run_file_transfer_stream.py")
    manifest_file = args.manifest_file or args.segment_dir / "manifest.json"
    metrics_file = args.metrics_file or args.segment_dir / "metrics.jsonl"
    command = [
        sys.executable,
        str(script),
        "--input-file",
        str(args.input_file),
        "--output-file",
        str(args.output_file),
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
        "--rx-frame-copies",
        str(args.rx_frame_copies),
        "--min-rx-rms-dbfs",
        str(args.min_rx_rms_dbfs),
        "--rx-level-retries",
        str(args.rx_level_retries),
        "--write-progressive",
        "--segment-dir",
        str(args.segment_dir),
        "--manifest-file",
        str(manifest_file),
        "--metrics-file",
        str(metrics_file),
    ]
    print(f"video_segment_mode=true")
    print(f"video_input_file={args.input_file}")
    print(f"video_output_file={args.output_file}")
    print(f"video_segment_dir={args.segment_dir}")
    print(f"video_manifest_file={manifest_file}")
    print(f"video_metrics_file={metrics_file}")
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
