from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.sources import IqReplaySource, SyntheticTargetSource
from sdr_loopback.radar.storage import append_metrics, save_frame


def target_argument(value: str) -> tuple[float, float, float]:
    try:
        values = tuple(float(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("target must be RANGE_M,VELOCITY_MPS,SNR_DB") from error
    if len(values) != 3:
        raise argparse.ArgumentTypeError("target must be RANGE_M,VELOCITY_MPS,SNR_DB")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process finite FMCW radar CPIs.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--replay", type=Path, action="append", default=[])
    parser.add_argument("--loop-replay", action="store_true")
    parser.add_argument("--target", type=target_argument, action="append", default=[])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--carrier-hz", type=float, default=76e9)
    parser.add_argument("--sample-rate-hz", type=float, default=30e6)
    parser.add_argument("--bandwidth-hz", type=float, default=20e6)
    parser.add_argument("--active-time-us", type=float, default=128.0)
    parser.add_argument("--idle-time-us", type=float, default=16.0)
    parser.add_argument("--chirp-count", type=int, default=64)
    parser.add_argument("--range-fft-size", type=int, default=4096)
    parser.add_argument("--doppler-fft-size", type=int, default=64)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.frames < 1:
        raise SystemExit("--frames must be positive")
    if args.replay and args.target:
        raise SystemExit("--target cannot be combined with --replay")

    if args.replay:
        source = IqReplaySource(args.replay, loop=args.loop_replay)
        processor = None
    else:
        config = RadarConfig(
            carrier_hz=args.carrier_hz,
            sample_rate_hz=args.sample_rate_hz,
            bandwidth_hz=args.bandwidth_hz,
            active_time_s=args.active_time_us * 1e-6,
            idle_time_s=args.idle_time_us * 1e-6,
            chirp_count=args.chirp_count,
            range_fft_size=args.range_fft_size,
            doppler_fft_size=args.doppler_fft_size,
        )
        targets = tuple(
            SyntheticTarget(f"T{index:02d}", range_m, velocity_mps, snr_db=snr_db)
            for index, (range_m, velocity_mps, snr_db) in enumerate(args.target, 1)
        )
        source = SyntheticTargetSource(config, targets, seed=args.seed)
        processor = FmcwProcessor(config)

    processed = 0
    source.open()
    try:
        for _ in range(args.frames):
            try:
                capture = source.capture()
            except StopIteration:
                break
            if processor is None or processor.config != capture.config:
                processor = FmcwProcessor(capture.config)
            frame = processor.process(capture)
            save_frame(args.output_dir, frame)
            append_metrics(args.output_dir / "metrics.jsonl", frame.diagnostics)
            processed += 1
    finally:
        source.close()

    print(f"frames_processed={processed}")
    print(f"output_dir={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
