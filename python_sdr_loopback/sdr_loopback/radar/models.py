"""Immutable data models shared by FMCW radar sources and processing."""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .config import RadarConfig


@dataclass(frozen=True)
class SyntheticTarget:
    target_id: str
    range_m: float
    radial_velocity_mps: float
    amplitude: float = 1.0
    azimuth_deg: Optional[float] = None
    snr_db: float = 20.0

    def doppler_hz(self, config: RadarConfig) -> float:
        return 2.0 * self.radial_velocity_mps / config.wavelength_m


@dataclass(frozen=True)
class RadarTarget:
    target_id: str
    range_m: float
    radial_velocity_mps: float
    azimuth_deg: Optional[float]
    snr_db: float
    power_db: float
    range_bin: int
    doppler_bin: int
    confidence: float
    timestamp: float


@dataclass(frozen=True)
class RadarDiagnostics:
    frame_index: int
    source: str
    sync_ok: bool
    sync_score: float
    phase_consistency: float
    rms: float
    peak: float
    clipping: bool
    clip_ratio: float
    noise_floor_db: float
    processing_time_ms: float
    overruns: int


@dataclass(frozen=True)
class RadarCapture:
    timestamp: float
    config: RadarConfig
    tx_iq: np.ndarray = field(compare=False, repr=False)
    rx_iq: np.ndarray = field(compare=False, repr=False)
    truth_targets: tuple[SyntheticTarget, ...] = ()
    chirp_start_sample: int = 0

    def __post_init__(self) -> None:
        tx_iq = np.asarray(self.tx_iq)
        rx_iq = np.asarray(self.rx_iq)
        if not isinstance(self.chirp_start_sample, (int, np.integer)):
            raise ValueError("chirp_start_sample must be an integer")
        chirp_start_sample = int(self.chirp_start_sample)
        if chirp_start_sample < 0:
            raise ValueError("chirp_start_sample must be nonnegative")
        expected = (self.config.cpi_samples,)
        if tx_iq.shape != expected:
            raise ValueError(f"tx_iq must have shape {expected}, got {tx_iq.shape}")
        minimum_rx_samples = chirp_start_sample + self.config.cpi_samples
        if rx_iq.ndim != 1 or rx_iq.size < minimum_rx_samples:
            raise ValueError(
                "rx_iq must be one-dimensional with at least "
                f"{minimum_rx_samples} samples, got shape {rx_iq.shape}"
            )
        if not np.iscomplexobj(rx_iq):
            raise ValueError("rx_iq must be a complex array")
        object.__setattr__(self, "tx_iq", tx_iq)
        object.__setattr__(self, "rx_iq", rx_iq)
        object.__setattr__(self, "chirp_start_sample", chirp_start_sample)


@dataclass(frozen=True)
class RadarFrame:
    frame_index: int
    timestamp: float
    config_snapshot: RadarConfig
    range_axis_m: np.ndarray = field(compare=False, repr=False)
    velocity_axis_mps: np.ndarray = field(compare=False, repr=False)
    range_doppler_db: np.ndarray = field(compare=False, repr=False)
    targets: tuple[RadarTarget, ...]
    diagnostics: RadarDiagnostics

    def __post_init__(self) -> None:
        range_axis = np.asarray(self.range_axis_m)
        velocity_axis = np.asarray(self.velocity_axis_mps)
        range_doppler = np.asarray(self.range_doppler_db)
        range_bin_count = self.config_snapshot.range_fft_size // 2 + 1
        if range_axis.shape != (range_bin_count,):
            raise ValueError("range_axis_m has an invalid shape")
        if velocity_axis.shape != (self.config_snapshot.doppler_fft_size,):
            raise ValueError("velocity_axis_mps has an invalid shape")
        expected_map_shape = (
            self.config_snapshot.doppler_fft_size,
            range_bin_count,
        )
        if range_doppler.shape != expected_map_shape:
            raise ValueError("range_doppler_db has an invalid shape")
        if self.diagnostics.frame_index != self.frame_index:
            raise ValueError("diagnostics frame_index does not match frame")
        for target in self.targets:
            if not (0 <= target.range_bin < range_axis.size):
                raise ValueError("target range_bin is outside the frame")
            if not (0 <= target.doppler_bin < velocity_axis.size):
                raise ValueError("target doppler_bin is outside the frame")
            if target.timestamp != self.timestamp:
                raise ValueError("target timestamp does not match frame")
            if not np.isclose(target.range_m, range_axis[target.range_bin]):
                raise ValueError("target range does not match its frame bin")
            if not np.isclose(
                target.radial_velocity_mps, velocity_axis[target.doppler_bin]
            ):
                raise ValueError("target velocity does not match its frame bin")
        object.__setattr__(self, "range_axis_m", range_axis)
        object.__setattr__(self, "velocity_axis_mps", velocity_axis)
        object.__setattr__(self, "range_doppler_db", range_doppler)
