"""Configuration and data contracts for the FMCW radar pipeline."""

from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .models import RadarCapture, RadarDiagnostics, RadarFrame, RadarTarget, SyntheticTarget
from .processor import FmcwProcessor
from .synchronizer import ChirpSynchronizer, ChirpSyncError, ChirpSyncResult

__all__ = [
    "ChirpSynchronizer",
    "ChirpSyncError",
    "ChirpSyncResult",
    "FmcwProcessor",
    "RadarCapture",
    "RadarConfig",
    "RadarDiagnostics",
    "RadarFrame",
    "RadarTarget",
    "SPEED_OF_LIGHT_MPS",
    "SyntheticTarget",
]
