"""Chirp-boundary synchronization for time-domain FMCW captures."""

from dataclasses import dataclass, replace

import numpy as np

from .config import RadarConfig
from .waveform import generate_active_chirp


@dataclass(frozen=True)
class ChirpSyncDiagnostics:
    failed_metric: str
    min_correlation: float
    mean_correlation: float
    periodic_coherence: float | None
    idle_to_active_db: float
    min_correlation_threshold: float
    mean_correlation_threshold: float
    periodic_coherence_threshold: float


class ChirpSyncError(ValueError):
    """Raised when a capture cannot provide a validated coherent interval."""

    def __init__(
        self,
        message: str,
        diagnostics: ChirpSyncDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class ChirpSyncResult:
    start_sample: int
    chirp_starts: np.ndarray
    correlation: float
    mean_correlation: float
    periodic_coherence: float | None
    idle_to_active_db: float


class ChirpSynchronizer:
    """Locate one complete CPI using metadata or bounded correlation."""

    _MIN_CHIRP_CORRELATION = 0.05
    _MIN_MEAN_CORRELATION = 0.08
    _MIN_PERIODIC_COHERENCE = 0.0075

    def __init__(self, config: RadarConfig, *, mode: str = "known") -> None:
        if mode not in {"known", "correlation"}:
            raise ValueError("mode must be 'known' or 'correlation'")
        if mode == "correlation" and (
            config.chirp_count < 2 or config.active_samples < 16
        ):
            raise ValueError(
                "correlation mode requires at least 2 chirps and 16 active samples"
            )
        self.config = config
        self.mode = mode

    def synchronize(
        self, capture: object, *, known_start_sample: int | None = None
    ) -> ChirpSyncResult:
        tx_iq = np.asarray(getattr(capture, "tx_iq"))
        rx_iq = np.asarray(getattr(capture, "rx_iq"))
        if tx_iq.ndim != 1 or rx_iq.ndim != 1:
            raise ChirpSyncError("capture IQ must be one-dimensional")
        if not np.all(np.isfinite(tx_iq)) or not np.all(np.isfinite(rx_iq)):
            raise ChirpSyncError("capture IQ must contain only finite samples")
        if rx_iq.size < self.config.cpi_samples:
            raise ChirpSyncError("capture does not contain all complete chirps")

        if self.mode == "known":
            start_sample = (
                getattr(capture, "chirp_start_sample", 0)
                if known_start_sample is None
                else known_start_sample
            )
            if not isinstance(start_sample, (int, np.integer)) or start_sample < 0:
                raise ChirpSyncError("known chirp start sample must be nonnegative")
            start_sample = int(start_sample)
        else:
            start_sample = self._correlate_start(rx_iq)
        chirp_starts = start_sample + (
            np.arange(self.config.chirp_count, dtype=np.int64)
            * self.config.samples_per_chirp
        )
        if start_sample + self.config.cpi_samples > rx_iq.size:
            raise ChirpSyncError("capture does not contain all complete chirps")

        correlations, idle_to_active_db = self._validate_chirps(rx_iq, chirp_starts)
        correlation = float(np.min(correlations))
        mean_correlation = float(np.mean(correlations))
        rx_cpi = rx_iq[start_sample : start_sample + self.config.cpi_samples]
        periodic_coherence = (
            self._periodic_coherence(rx_cpi)
            if self.config.chirp_count >= 2
            else None
        )
        if self.mode == "correlation":
            diagnostics = ChirpSyncDiagnostics(
                failed_metric="",
                min_correlation=correlation,
                mean_correlation=mean_correlation,
                periodic_coherence=periodic_coherence,
                idle_to_active_db=idle_to_active_db,
                min_correlation_threshold=self._MIN_CHIRP_CORRELATION,
                mean_correlation_threshold=self._MIN_MEAN_CORRELATION,
                periodic_coherence_threshold=self._MIN_PERIODIC_COHERENCE,
            )
            if correlation < self._MIN_CHIRP_CORRELATION:
                raise ChirpSyncError(
                    "chirp correlation is below the sync threshold "
                    f"(min_correlation={correlation:.4f} < "
                    f"{self._MIN_CHIRP_CORRELATION:.4f})",
                    replace(diagnostics, failed_metric="min_correlation"),
                )
            if mean_correlation < self._MIN_MEAN_CORRELATION:
                raise ChirpSyncError(
                    "mean chirp correlation is below the sync threshold "
                    f"(mean_correlation={mean_correlation:.4f} < "
                    f"{self._MIN_MEAN_CORRELATION:.4f})",
                    replace(diagnostics, failed_metric="mean_correlation"),
                )
            if periodic_coherence < self._MIN_PERIODIC_COHERENCE:
                raise ChirpSyncError(
                    "chirp periodic coherence is below the sync threshold "
                    f"(periodic_coherence={periodic_coherence:.4f} < "
                    f"{self._MIN_PERIODIC_COHERENCE:.4f})",
                    replace(diagnostics, failed_metric="periodic_coherence"),
                )

        chirp_starts.setflags(write=False)
        return ChirpSyncResult(
            start_sample=start_sample,
            chirp_starts=chirp_starts,
            correlation=correlation,
            mean_correlation=mean_correlation,
            periodic_coherence=periodic_coherence,
            idle_to_active_db=idle_to_active_db,
        )

    def _correlate_start(self, rx_iq: np.ndarray) -> int:
        reference = generate_active_chirp(self.config).astype(np.complex128)
        max_complete_start = rx_iq.size - self.config.cpi_samples
        search_limit = min(self.config.samples_per_chirp, max_complete_start)
        search_data = np.asarray(
            rx_iq[: search_limit + reference.size], dtype=np.complex128
        )

        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            correlation = np.correlate(search_data, reference, mode="valid")
            power = np.square(np.abs(search_data), dtype=np.float64)
            cumulative_power = np.concatenate(([0.0], np.cumsum(power)))
            window_power = (
                cumulative_power[reference.size :]
                - cumulative_power[: -reference.size]
            )
            denominator = np.sqrt(
                window_power * float(np.vdot(reference, reference).real)
            )
            scores = np.divide(
                np.abs(correlation),
                denominator,
                out=np.zeros_like(denominator),
                where=denominator > 0.0,
            )
        if (
            not np.all(np.isfinite(correlation))
            or not np.all(np.isfinite(window_power))
            or not np.all(np.isfinite(denominator))
            or not np.all(np.isfinite(scores))
        ):
            raise ChirpSyncError("correlation metric must be finite")
        return int(np.argmax(scores))

    def _validate_chirps(
        self, rx_iq: np.ndarray, chirp_starts: np.ndarray
    ) -> tuple[np.ndarray, float]:
        rx_iq = np.asarray(rx_iq, dtype=np.complex128)
        reference = generate_active_chirp(self.config).astype(np.complex128)
        reference_energy = float(np.vdot(reference, reference).real)
        correlations = np.empty(self.config.chirp_count, dtype=np.float64)
        active_energy = 0.0
        idle_energy = 0.0
        idle_samples = self.config.samples_per_chirp - self.config.active_samples

        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            for index, start in enumerate(chirp_starts):
                active = rx_iq[start : start + self.config.active_samples]
                energy = float(np.vdot(active, active).real)
                denominator = np.sqrt(energy * reference_energy)
                correlations[index] = (
                    abs(np.vdot(reference, active)) / denominator
                    if denominator
                    else 0.0
                )
                if not np.isfinite(energy) or not np.isfinite(correlations[index]):
                    raise ChirpSyncError("correlation metric must be finite")
                active_energy += energy
                if idle_samples:
                    idle = rx_iq[
                        start + self.config.active_samples : start
                        + self.config.samples_per_chirp
                    ]
                    idle_chirp_energy = float(np.vdot(idle, idle).real)
                    if not np.isfinite(idle_chirp_energy):
                        raise ChirpSyncError("idle energy metric must be finite")
                    idle_energy += idle_chirp_energy

            mean_active_power = active_energy / (
                self.config.chirp_count * self.config.active_samples
            )
            mean_idle_power = (
                idle_energy / (self.config.chirp_count * idle_samples)
                if idle_samples
                else 0.0
            )
            ratio = mean_idle_power / max(mean_active_power, np.finfo(float).tiny)
            idle_to_active_db = 10.0 * np.log10(
                max(ratio, np.finfo(float).tiny)
            )
        if (
            not np.isfinite(mean_active_power)
            or not np.isfinite(mean_idle_power)
            or not np.isfinite(ratio)
            or not np.isfinite(idle_to_active_db)
        ):
            raise ChirpSyncError("idle energy metric must be finite")
        return correlations, float(idle_to_active_db)

    def _periodic_coherence(self, rx_cpi: np.ndarray) -> float:
        values = np.asarray(rx_cpi, dtype=np.complex128)
        period = self.config.samples_per_chirp
        first = values[:-period]
        second = values[period:]
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            numerator = abs(np.vdot(first, second))
            denominator = np.sqrt(
                float(np.vdot(first, first).real * np.vdot(second, second).real)
            )
            coherence = numerator / denominator if denominator else 0.0
        if not np.isfinite(coherence):
            raise ChirpSyncError("periodic coherence metric must be finite")
        return float(np.clip(coherence, 0.0, 1.0))
