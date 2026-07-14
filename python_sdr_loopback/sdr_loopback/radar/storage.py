"""Portable persistence for FMCW captures and processed frames."""

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from .config import RadarConfig
from .models import RadarCapture, RadarDiagnostics, RadarFrame, SyntheticTarget


def save_capture(path: str | Path, capture: RadarCapture) -> None:
    """Save a capture as a compressed, pickle-free NPZ archive."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields: dict[str, object] = {
        "tx_iq": np.asarray(capture.tx_iq, dtype=np.complex64),
        "rx_iq": np.asarray(capture.rx_iq, dtype=np.complex64),
        "radar_config_json": json.dumps(asdict(capture.config)),
        "capture_timestamp": float(capture.timestamp),
    }
    if capture.truth_targets:
        fields["truth_targets_json"] = json.dumps(
            [asdict(target) for target in capture.truth_targets]
        )
    np.savez_compressed(output, **fields)


def load_capture(path: str | Path) -> RadarCapture:
    """Load and validate a capture saved by :func:`save_capture`."""
    with np.load(Path(path), allow_pickle=False) as archive:
        required = ("tx_iq", "rx_iq", "radar_config_json", "capture_timestamp")
        for field_name in required:
            if field_name not in archive.files:
                raise ValueError(f"capture missing required field: {field_name}")
        try:
            config_values = json.loads(str(archive["radar_config_json"].item()))
            if not isinstance(config_values, dict):
                raise TypeError
            config = RadarConfig(**config_values)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as error:
            raise ValueError("invalid radar_config_json") from error
        try:
            truth_values = json.loads(
                str(archive["truth_targets_json"].item())
                if "truth_targets_json" in archive.files
                else "[]"
            )
            if not isinstance(truth_values, list):
                raise TypeError
            truth = tuple(SyntheticTarget(**values) for values in truth_values)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as error:
            raise ValueError("invalid truth_targets_json") from error
        tx_iq = np.asarray(archive["tx_iq"])
        rx_iq = np.asarray(archive["rx_iq"])
        if tx_iq.ndim != 1 or rx_iq.ndim != 1 or tx_iq.size != rx_iq.size:
            raise ValueError("tx_iq and rx_iq lengths do not match")
        if tx_iq.size != config.cpi_samples:
            raise ValueError("IQ sample count does not match radar config")
        return RadarCapture(
            timestamp=float(archive["capture_timestamp"].item()),
            config=config,
            tx_iq=np.asarray(tx_iq, dtype=np.complex64),
            rx_iq=np.asarray(rx_iq, dtype=np.complex64),
            truth_targets=truth,
        )


def save_frame(path: str | Path, frame: RadarFrame) -> None:
    """Write independently loadable JSON metadata and NumPy heatmap files."""
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "frame_index": frame.frame_index,
        "timestamp": frame.timestamp,
        "radar_config": asdict(frame.config_snapshot),
        "range_axis_m": frame.range_axis_m.tolist(),
        "velocity_axis_mps": frame.velocity_axis_mps.tolist(),
        "targets": [asdict(target) for target in frame.targets],
        "diagnostics": asdict(frame.diagnostics),
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    np.save(output / "range_doppler.npy", frame.range_doppler_db, allow_pickle=False)


def append_metrics(path: str | Path, diagnostics: RadarDiagnostics) -> None:
    """Append one complete diagnostics JSON object to a JSON Lines file."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(asdict(diagnostics), allow_nan=False, separators=(",", ":"))
    with output.open("a", encoding="utf-8", newline="") as stream:
        stream.write(serialized + "\n")
