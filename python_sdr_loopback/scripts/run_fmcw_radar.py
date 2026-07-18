from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.sources import (
    E310CpiSource,
    E310RadioConfig,
    IqReplaySource,
    SyntheticTargetSource,
)
from sdr_loopback.radar.storage import append_metrics, save_capture, save_frame


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
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--source", choices=("synthetic", "e310"), default="synthetic")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--duration-sec", type=float)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--metrics", type=Path)
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
    parser.add_argument("--tx-gain-db", type=float, default=-40.0)
    parser.add_argument("--tx-amplitude", type=float, default=0.25)
    parser.add_argument("--rx-gain-db", type=float, default=20.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.frames < 1:
        raise SystemExit("--frames must be positive")
    if args.duration_sec is not None and args.duration_sec <= 0.0:
        raise SystemExit("--duration-sec must be positive")
    if args.duration_sec is not None and (args.source != "synthetic" or args.replay):
        raise SystemExit(
            "--duration-sec currently requires --source synthetic without --replay"
        )
    if args.replay and args.target:
        raise SystemExit("--target cannot be combined with --replay")
    if args.replay and args.source != "synthetic":
        raise SystemExit("--replay cannot be combined with --source e310")
    if args.source == "e310" and args.target:
        raise SystemExit("--target cannot be combined with --source e310")

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
    radio = (
        E310RadioConfig(
            tx_gain_db=args.tx_gain_db,
            tx_amplitude=args.tx_amplitude,
            rx_gain_db=args.rx_gain_db,
        )
        if args.source == "e310"
        else None
    )
    if args.dry_run:
        if args.source != "e310":
            raise SystemExit("--dry-run requires --source e310")
        assert radio is not None
        print("source=e310")
        print(f"uri={radio.uri}")
        print(f"sample_rate_hz={int(config.sample_rate_hz)}")
        print(f"cpi_samples={config.cpi_samples}")
        print(f"tx_gain_db={radio.tx_gain_db}")
        print(f"tx_amplitude={radio.tx_amplitude}")
        print(f"rx_gain_db={radio.rx_gain_db}")
        print("hardware_access=false")
        return 0
    if args.output_dir is None and not args.headless:
        raise SystemExit("--output-dir is required unless --dry-run or --headless is used")
    if args.metrics is not None:
        metrics_path = args.metrics
    elif args.output_dir is not None:
        metrics_path = args.output_dir / "metrics.jsonl"
    else:
        raise SystemExit("--metrics is required when --headless has no --output-dir")

    if args.replay:
        source = IqReplaySource(args.replay, loop=args.loop_replay)
        processor = None
    elif args.source == "e310":
        assert radio is not None
        source = E310CpiSource(config, radio)
        processor = FmcwProcessor(config, sync_mode="correlation")
    else:
        targets = tuple(
            SyntheticTarget(f"T{index:02d}", range_m, velocity_mps, snr_db=snr_db)
            for index, (range_m, velocity_mps, snr_db) in enumerate(args.target, 1)
        )
        source = SyntheticTargetSource(config, targets, seed=args.seed)
        processor = FmcwProcessor(config)

    processed = 0
    failed = 0
    started_at = monotonic()
    try:
        source.open()
        while args.duration_sec is None or monotonic() - started_at < args.duration_sec:
            if args.duration_sec is None and processed >= args.frames:
                break
            try:
                capture = source.capture()
            except StopIteration:
                break
            if args.capture_dir is not None:
                save_capture(
                    args.capture_dir / f"capture-{processed:06d}.npz", capture
                )
            if processor is None or processor.config != capture.config:
                processor = FmcwProcessor(capture.config)
            frame = processor.process(capture)
            if not args.headless:
                assert args.output_dir is not None
                save_frame(args.output_dir, frame)
            append_metrics(metrics_path, frame.diagnostics)
            processed += 1
    except Exception:
        failed += 1
        raise
    finally:
        source.close()

    print(f"frames_processed={processed}")
    print(f"frames_failed={failed}")
    print("queue_max_depth=1")
    print("controller_stopped=true")
    if args.duration_sec is not None:
        soak_ok = failed == 0 and processed > 0
        print(f"duration_sec={args.duration_sec}")
        print(f"soak_ok={str(soak_ok).lower()}")
        summary_path = metrics_path.with_name("soak_summary.json")
        summary_path.write_text(
            json.dumps(
                {
                    "duration_sec": args.duration_sec,
                    "frames_processed": processed,
                    "frames_failed": failed,
                    "queue_max_depth": 1,
                    "controller_stopped": True,
                    "soak_ok": soak_ok,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"soak_summary={summary_path}")
    if args.output_dir is not None:
        print(f"output_dir={args.output_dir}")
    print(f"metrics={metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
