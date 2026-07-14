"""Chirp-boundary synchronization for time-domain FMCW captures."""

from dataclasses import dataclass

import numpy as np

from .config import RadarConfig
from .waveform import generate_active_chirp


class ChirpSyncError(ValueError):
    """Raised when a capture cannot provide a validated coherent interval."""


@dataclass(frozen=True)
class ChirpSyncResult:
    start_sample: int
    chirp_starts: np.ndarray
    correlation: float
    idle_to_active_db: float


class ChirpSynchronizer:
    """Locate one complete CPI using metadata or bounded correlation."""

    _MIN_CORRELATION = 0.8
    _MAX_IDLE_TO_ACTIVE_DB = -6.0

    def __init__(self, config: RadarConfig, *, mode: str = "known") -> None:
        if mode not in {"known", "correlation"}:
            raise ValueError("mode must be 'known' or 'correlation'")
        self.config = config
        self.mode = mode

    def synchronize(self, capture: object) -> ChirpSyncResult:
        tx_iq = np.asarray(getattr(capture, "tx_iq"))
        rx_iq = np.asarray(getattr(capture, "rx_iq"))
        if tx_iq.ndim != 1 or rx_iq.ndim != 1:
            raise ChirpSyncError("capture IQ must be one-dimensional")
        if not np.all(np.isfinite(tx_iq)) or not np.all(np.isfinite(rx_iq)):
            raise ChirpSyncError("capture IQ must contain only finite samples")
        if rx_iq.size < self.config.cpi_samples:
            raise ChirpSyncError("capture does not contain all complete chirps")

        start_sample = 0 if self.mode == "known" else self._correlate_start(rx_iq)
        chirp_starts = start_sample + (
            np.arange(self.config.chirp_count, dtype=np.int64)
            * self.config.samples_per_chirp
        )
        if start_sample + self.config.cpi_samples > rx_iq.size:
            raise ChirpSyncError("capture does not contain all complete chirps")

        correlations, idle_to_active_db = self._validate_chirps(rx_iq, chirp_starts)
        correlation = float(np.min(correlations))
        if self.mode == "correlation":
            if correlation < self._MIN_CORRELATION:
                raise ChirpSyncError("chirp correlation is below the sync threshold")
            if idle_to_active_db > self._MAX_IDLE_TO_ACTIVE_DB:
                raise ChirpSyncError("idle energy is too high for reliable chirp sync")

        chirp_starts.setflags(write=False)
        return ChirpSyncResult(
            start_sample=start_sample,
            chirp_starts=chirp_starts,
            correlation=correlation,
            idle_to_active_db=idle_to_active_db,
        )

    def _correlate_start(self, tx_iq: np.ndarray) -> int:
        reference = generate_active_chirp(self.config).astype(np.complex128)
        max_complete_start = tx_iq.size - self.config.cpi_samples
        search_limit = min(self.config.samples_per_chirp, max_complete_start)
        search_data = tx_iq[: search_limit + reference.size]

        correlation = np.correlate(search_data, reference, mode="valid")
        power = np.abs(search_data) ** 2
        cumulative_power = np.concatenate(([0.0], np.cumsum(power)))
        window_power = (
            cumulative_power[reference.size :] - cumulative_power[: -reference.size]
        )
        denominator = np.sqrt(window_power * np.vdot(reference, reference).real)
        scores = np.divide(
            np.abs(correlation),
            denominator,
            out=np.zeros_like(denominator),
            where=denominator > 0.0,
        )
        return int(np.argmax(scores))

    def _validate_chirps(
        self, tx_iq: np.ndarray, chirp_starts: np.ndarray
    ) -> tuple[np.ndarray, float]:
        reference = generate_active_chirp(self.config).astype(np.complex128)
        reference_energy = float(np.vdot(reference, reference).real)
        correlations = np.empty(self.config.chirp_count, dtype=np.float64)
        active_energy = 0.0
        idle_energy = 0.0
        idle_samples = self.config.samples_per_chirp - self.config.active_samples

        for index, start in enumerate(chirp_starts):
            active = tx_iq[start : start + self.config.active_samples]
            energy = float(np.vdot(active, active).real)
            denominator = np.sqrt(energy * reference_energy)
            correlations[index] = (
                abs(np.vdot(reference, active)) / denominator if denominator else 0.0
            )
            active_energy += energy
            if idle_samples:
                idle = tx_iq[
                    start + self.config.active_samples : start
                    + self.config.samples_per_chirp
                ]
                idle_energy += float(np.vdot(idle, idle).real)

        mean_active_power = active_energy / (
            self.config.chirp_count * self.config.active_samples
        )
        mean_idle_power = (
            idle_energy / (self.config.chirp_count * idle_samples)
            if idle_samples
            else 0.0
        )
        ratio = mean_idle_power / max(mean_active_power, np.finfo(float).tiny)
        idle_to_active_db = 10.0 * np.log10(max(ratio, np.finfo(float).tiny))
        return correlations, float(idle_to_active_db)
