from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig


def positive_duration(value: str) -> float:
    duration = float(value)
    if not math.isfinite(duration) or not 0.0 < duration <= 300.0:
        raise argparse.ArgumentTypeError("duration must be finite and in (0, 300] seconds")
    return duration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transmit cyclic FMCW for scope diagnostics."
    )
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration-s", type=positive_duration, default=None)
    duration.add_argument(
        "--continuous",
        action="store_true",
        help="Transmit until Ctrl+C is pressed.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    duration_s = None if args.continuous else (args.duration_s or 15.0)
    config = RadarConfig()
    radio = E310RadioConfig(
        tx_gain_db=-30.0,
        tx_amplitude=0.40,
        rx_gain_db=50.0,
    )
    if args.dry_run:
        print("source=e310")
        print(f"uri={radio.uri}")
        print(f"tx_lo_hz={radio.lo_hz}")
        print(f"tx_gain_db={radio.tx_gain_db}")
        print(f"tx_amplitude={radio.tx_amplitude}")
        print(f"duration_s={'continuous' if duration_s is None else duration_s}")
        print("hardware_access=false")
        return 0

    source = E310CpiSource(config, radio)
    interrupted = False
    try:
        print("connecting_to_e310=true", flush=True)
        source.open()
        print("tx_enabled=true", flush=True)
        print(
            f"duration_s={'continuous' if duration_s is None else duration_s}",
            flush=True,
        )
        if duration_s is None:
            while True:
                time.sleep(3600.0)
        else:
            time.sleep(duration_s)
    except KeyboardInterrupt:
        interrupted = True
        print("tx_interrupted=true", flush=True)
    finally:
        source.close()
        print("tx_enabled=false", flush=True)
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
