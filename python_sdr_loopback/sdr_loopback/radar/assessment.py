"""Rank and persist manual corner-reflector placement candidates."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .models import RadarFrame, RadarTarget


@dataclass(frozen=True)
class PositionCandidate:
    """One manually labelled physical placement and its radar evidence."""

    frame_index: int
    timestamp: float
    label: str
    measured_range_m: float | None
    notes: str
    photo_path: str | None
    quality_score: float
    target_count: int
    detected_range_m: float | None
    detected_velocity_mps: float | None
    target_snr_db: float | None
    target_confidence: float | None
    sync_score: float
    phase_consistency: float
    clipping: bool
    noise_floor_db: float


def _best_target(frame: RadarFrame) -> RadarTarget | None:
    return max(
        frame.targets,
        key=lambda target: (target.confidence, target.snr_db, target.power_db),
        default=None,
    )


def assess_frame(
    frame: RadarFrame,
    *,
    label: str = "",
    measured_range_m: float | None = None,
    notes: str = "",
    photo_path: str | None = None,
) -> PositionCandidate:
    """Create a repeatable quality score for comparing physical placements."""
    if measured_range_m is not None and measured_range_m < 0.0:
        raise ValueError("measured_range_m must be nonnegative")
    target = _best_target(frame)
    diagnostics = frame.diagnostics
    if target is None or diagnostics.clipping:
        score = 0.0
    else:
        score = min(1.0, target.confidence) * 40.0
        score += min(1.0, max(0.0, target.snr_db) / 30.0) * 30.0
        score += min(1.0, max(0.0, diagnostics.sync_score) / 0.2) * 15.0
        score += min(1.0, max(0.0, diagnostics.phase_consistency)) * 15.0
    return PositionCandidate(
        frame_index=frame.frame_index,
        timestamp=frame.timestamp,
        label=label.strip(),
        measured_range_m=measured_range_m,
        notes=notes.strip(),
        photo_path=str(photo_path) if photo_path else None,
        quality_score=round(score, 1),
        target_count=len(frame.targets),
        detected_range_m=target.range_m if target is not None else None,
        detected_velocity_mps=target.radial_velocity_mps if target is not None else None,
        target_snr_db=target.snr_db if target is not None else None,
        target_confidence=target.confidence if target is not None else None,
        sync_score=diagnostics.sync_score,
        phase_consistency=diagnostics.phase_consistency,
        clipping=diagnostics.clipping,
        noise_floor_db=diagnostics.noise_floor_db,
    )


def append_position_candidate(path: str | Path, candidate: PositionCandidate) -> None:
    """Append one candidate as a portable JSON Lines record."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(asdict(candidate), allow_nan=False) + "\n")
