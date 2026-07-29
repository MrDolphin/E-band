from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.simulator import simulate_capture
from sdr_loopback.radar.storage import save_capture


def target_argument(value: str) -> SyntheticTarget:
    try:
        range_m, velocity_mps, snr_db = (float(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("target must be RANGE_M,VELOCITY_MPS,SNR_DB") from error
    return SyntheticTarget("", range_m, velocity_mps, snr_db=snr_db)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a synthetic FMCW capture fixture.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", action="append", type=target_argument, default=[])
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
        SyntheticTarget(
            target_id=f"T{index:02d}",
            range_m=target.range_m,
            radial_velocity_mps=target.radial_velocity_mps,
            snr_db=target.snr_db,
        )
        for index, target in enumerate(args.target, 1)
    )
    capture = simulate_capture(config, targets, seed=args.seed)
    save_capture(args.output, capture)
    print(f"active_samples={config.active_samples}")
    print(f"chirp_count={config.chirp_count}")
    print(f"truth_targets={len(targets)}")
    print(f"capture={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
