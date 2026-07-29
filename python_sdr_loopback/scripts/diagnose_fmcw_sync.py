"""Capture repeatable E310 FMCW synchronization diagnostics."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import (
    RadarConfig,
    fmcw_profile_names,
    fmcw_profile_values,
    recommended_range_fft_size,
)
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig
from sdr_loopback.radar.storage import save_capture
from sdr_loopback.radar.synchronizer import ChirpSyncError, ChirpSynchronizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture E310 FMCW sync metrics and failed raw IQ frames."
    )
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/hardware")
        / f"sync_diagnosis_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    parser.add_argument("--save-captures", choices=("all", "failures", "none"), default="failures")
    parser.add_argument("--profile", choices=fmcw_profile_names(), default="stable-20")
    parser.add_argument("--carrier-hz", type=float)
    parser.add_argument("--sample-rate-hz", type=float)
    parser.add_argument("--bandwidth-hz", type=float)
    parser.add_argument("--active-time-us", type=float)
    parser.add_argument("--idle-time-us", type=float)
    parser.add_argument("--chirp-count", type=int)
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--tx-channel", type=int, default=0)
    parser.add_argument("--rx-channel", type=int, default=0)
    parser.add_argument("--tx-port", default="B")
    parser.add_argument("--rx-port", default="B_BALANCED")
    parser.add_argument("--tx-gain-db", type=float, default=-20.0)
    parser.add_argument("--tx-amplitude", type=float, default=0.40)
    parser.add_argument("--rx-gain-db", type=float, default=20.0)
    parser.add_argument("--pre-tx-settle-s", type=float, default=1.0)
    parser.add_argument("--settle-s", type=float, default=0.25)
    parser.add_argument("--sync-margin-chirps", type=int, default=1)
    parser.add_argument("--startup-rx-discard-buffers", type=int, default=2)
    return parser


def build_config(args: argparse.Namespace) -> RadarConfig:
    values = fmcw_profile_values(args.profile)
    overrides = {
        "carrier_hz": args.carrier_hz,
        "sample_rate_hz": args.sample_rate_hz,
        "bandwidth_hz": args.bandwidth_hz,
        "active_time_s": (
            None if args.active_time_us is None else args.active_time_us * 1e-6
        ),
        "idle_time_s": (
            None if args.idle_time_us is None else args.idle_time_us * 1e-6
        ),
        "chirp_count": args.chirp_count,
    }
    values.update({name: value for name, value in overrides.items() if value is not None})
    return RadarConfig(
        **values,
        range_fft_size=recommended_range_fft_size(
            float(values["sample_rate_hz"]), float(values["active_time_s"])
        ),
    )


def build_radio(args: argparse.Namespace) -> E310RadioConfig:
    return E310RadioConfig(
        uri=args.uri,
        lo_hz=args.lo_hz,
        tx_channel=args.tx_channel,
        rx_channel=args.rx_channel,
        tx_port=args.tx_port,
        rx_port=args.rx_port,
        tx_gain_db=args.tx_gain_db,
        tx_amplitude=args.tx_amplitude,
        rx_gain_db=args.rx_gain_db,
        pre_tx_settle_s=args.pre_tx_settle_s,
        settle_s=args.settle_s,
        sync_margin_chirps=args.sync_margin_chirps,
        startup_rx_discard_buffers=args.startup_rx_discard_buffers,
    )


def dbfs(value: float) -> float | None:
    if not np.isfinite(value) or value <= 0.0:
        return None
    return float(20.0 * np.log10(value))


def print_plan(
    config: RadarConfig, radio: E310RadioConfig, frames: int, profile: str
) -> None:
    print("source=e310")
    print(f"profile={profile}")
    print(f"frames={frames}")
    print(f"uri={radio.uri}")
    print(f"sample_rate_hz={int(config.sample_rate_hz)}")
    print(f"bandwidth_hz={int(config.bandwidth_hz)}")
    print(f"cpi_samples={config.cpi_samples}")
    print(f"sync_margin_chirps={radio.sync_margin_chirps}")
    print(f"startup_rx_discard_buffers={radio.startup_rx_discard_buffers}")
    print(f"tx_gain_db={radio.tx_gain_db}")
    print(f"tx_amplitude={radio.tx_amplitude}")
    print(f"rx_gain_db={radio.rx_gain_db}")


def main() -> int:
    args = build_parser().parse_args()
    if args.frames < 1:
        raise SystemExit("--frames must be positive")
    config = build_config(args)
    radio = build_radio(args)
    print_plan(config, radio, args.frames, args.profile)
    if args.dry_run:
        print("hardware_access=false")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / "sync_metrics.jsonl"
    source = E310CpiSource(config, radio)
    synchronizer = ChirpSynchronizer(config, mode="correlation")
    synced = 0
    saved = 0
    try:
        source.open()
        for frame_index in range(args.frames):
            started = perf_counter()
            capture = source.capture()
            elapsed_ms = (perf_counter() - started) * 1000.0
            rx_iq = np.asarray(capture.rx_iq)
            magnitude = np.abs(rx_iq)
            peak = float(np.max(magnitude)) if magnitude.size else 0.0
            rms = float(np.sqrt(np.mean(np.square(magnitude)))) if magnitude.size else 0.0
            record = {
                "frame_index": frame_index,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "capture_time_ms": elapsed_ms,
                "rx_peak_dbfs": dbfs(peak),
                "rx_rms_dbfs": dbfs(rms),
                "rx_clipping_ratio": float(np.mean(magnitude >= 0.99)),
                "sync_ok": False,
                "sync_error": None,
                "start_sample": None,
                "correlation": None,
                "mean_correlation": None,
                "periodic_coherence": None,
                "idle_to_active_db": None,
            }
            try:
                result = synchronizer.synchronize(capture)
            except ChirpSyncError as error:
                record["sync_error"] = str(error)
            else:
                synced += 1
                record.update(
                    sync_ok=True,
                    start_sample=result.start_sample,
                    correlation=result.correlation,
                    mean_correlation=result.mean_correlation,
                    periodic_coherence=result.periodic_coherence,
                    idle_to_active_db=result.idle_to_active_db,
                )
            with metrics_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
            save = args.save_captures == "all" or (
                args.save_captures == "failures" and not record["sync_ok"]
            )
            if save:
                save_capture(args.output_dir / "captures" / f"capture-{frame_index:04d}.npz", capture)
                saved += 1
            status = "OK" if record["sync_ok"] else f"FAIL: {record['sync_error']}"
            print(f"frame={frame_index} sync={status} rms_dbfs={record['rx_rms_dbfs']} time_ms={elapsed_ms:.1f}")
    finally:
        source.close()
    print(f"frames_synced={synced}")
    print(f"frames_failed={args.frames - synced}")
    print(f"captures_saved={saved}")
    print(f"metrics_path={metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
