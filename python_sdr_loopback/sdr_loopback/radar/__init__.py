"""Configuration and data contracts for the FMCW radar pipeline."""

from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .detector import ca_cfar_2d, cluster_detections, detect_targets, local_peak_mask
from .models import RadarCapture, RadarDiagnostics, RadarFrame, RadarTarget, SyntheticTarget
from .processor import FmcwProcessor
from .synchronizer import ChirpSynchronizer, ChirpSyncError, ChirpSyncResult

__all__ = [
    "ChirpSynchronizer",
    "ChirpSyncError",
    "ChirpSyncResult",
    "ca_cfar_2d",
    "cluster_detections",
    "detect_targets",
    "FmcwProcessor",
    "RadarCapture",
    "RadarConfig",
    "RadarDiagnostics",
    "RadarFrame",
    "RadarTarget",
    "local_peak_mask",
    "SPEED_OF_LIGHT_MPS",
    "SyntheticTarget",
]
