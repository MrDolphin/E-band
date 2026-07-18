"""Offline acceptance checks for the deployable FMCW bandwidth profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import (
    RadarConfig,
    fmcw_profile_names,
    fmcw_profile_values,
    recommended_range_fft_size,
)
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.simulator import simulate_capture


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify synthetic target recovery for deployable FMCW profiles."
    )
    parser.add_argument("--profile", choices=fmcw_profile_names(), action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    return parser


def profile_config(profile: str) -> RadarConfig:
    values = fmcw_profile_values(profile)
    return RadarConfig(
        **values,
        range_fft_size=recommended_range_fft_size(
            float(values["sample_rate_hz"]), float(values["active_time_s"])
        ),
    )


def validate_profile(profile: str, seed: int) -> dict[str, object]:
    config = profile_config(profile)
    expected = SyntheticTarget(
        "T01", range_m=22.5, radial_velocity_mps=1.2, snr_db=18.0
    )
    frame = FmcwProcessor(config).process(simulate_capture(config, (expected,), seed=seed))
    range_tolerance = config.range_resolution_m
    velocity_tolerance = config.velocity_resolution_mps
    candidate = min(
        frame.targets,
        key=lambda target: (
            abs(target.range_m - expected.range_m),
            abs(target.radial_velocity_mps - expected.radial_velocity_mps),
        ),
        default=None,
    )
    range_error = None if candidate is None else abs(candidate.range_m - expected.range_m)
    velocity_error = (
        None
        if candidate is None
        else abs(candidate.radial_velocity_mps - expected.radial_velocity_mps)
    )
    passed = (
        candidate is not None
        and range_error is not None
        and velocity_error is not None
        and range_error <= range_tolerance
        and velocity_error <= velocity_tolerance
    )
    return {
        "profile": profile,
        "sample_rate_hz": config.sample_rate_hz,
        "bandwidth_hz": config.bandwidth_hz,
        "active_samples": config.active_samples,
        "range_fft_size": config.range_fft_size,
        "cpi_samples": config.cpi_samples,
        "target_count": len(frame.targets),
        "range_error_m": range_error,
        "range_tolerance_m": range_tolerance,
        "velocity_error_mps": velocity_error,
        "velocity_tolerance_mps": velocity_tolerance,
        "processing_time_ms": frame.diagnostics.processing_time_ms,
        "passed": passed,
    }


def main() -> int:
    args = build_parser().parse_args()
    profiles = args.profile or fmcw_profile_names()
    results = [validate_profile(profile, args.seed) for profile in profiles]
    report = {"hardware_access": False, "passed": all(row["passed"] for row in results), "profiles": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in results:
        print(f"profile={row['profile']} passed={str(row['passed']).lower()}")
    print(f"report={args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
