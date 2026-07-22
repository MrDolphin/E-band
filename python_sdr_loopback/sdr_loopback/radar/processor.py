"""Shared NumPy range-Doppler processing for FMCW receive IQ."""

from dataclasses import dataclass
import threading
from time import perf_counter

import numpy as np

from .calibration import BackgroundCalibration
from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .detector import detect_targets
from .models import RadarDiagnostics, RadarFrame
from .synchronizer import ChirpSynchronizer
from .waveform import generate_active_chirp


@dataclass(frozen=True)
class BackgroundCalibrationStatus:
    """Thread-safe snapshot of an in-session empty-room calibration."""

    active: bool
    collected_cpis: int
    required_cpis: int
    skipped_cpis: int
    coherence_restarts: int
    last_coherence: float | None
    ready: bool
    error: str | None


class FmcwProcessor:
    """Convert one synchronized time-domain CPI into a range-Doppler frame."""

    def __init__(
        self,
        config: RadarConfig,
        *,
        sync_mode: str = "known",
        calibration: object | None = None,
        velocity_trust_threshold: float = 0.5,
    ) -> None:
        if not np.isfinite(velocity_trust_threshold) or not (
            0.0 <= velocity_trust_threshold <= 1.0
        ):
            raise ValueError("velocity_trust_threshold must be finite and in [0, 1]")
        self.config = config
        self._synchronizer = ChirpSynchronizer(config, mode=sync_mode)
        self._calibration = calibration
        self._calibration_lock = threading.Lock()
        self._calibration_matrices: list[np.ndarray] = []
        self._calibration_required_cpis = 0
        self._calibration_collected_cpis = 0
        self._calibration_skipped_cpis = 0
        self._calibration_coherence_restarts = 0
        self._calibration_last_coherence: float | None = None
        self._calibration_error: str | None = None
        self._velocity_trust_threshold = velocity_trust_threshold
        self._frame_index = 0

    def begin_background_calibration(self, *, cpi_count: int = 16) -> None:
        """Replace any prior background after collecting synchronized empty-room CPIs."""
        if (
            not isinstance(cpi_count, (int, np.integer))
            or isinstance(cpi_count, (bool, np.bool_))
            or cpi_count <= 0
        ):
            raise ValueError("cpi_count must be a positive integer")
        with self._calibration_lock:
            self._calibration_matrices = []
            self._calibration_required_cpis = int(cpi_count)
            self._calibration_collected_cpis = 0
            self._calibration_skipped_cpis = 0
            self._calibration_coherence_restarts = 0
            self._calibration_last_coherence = None
            self._calibration_error = None

    def note_background_calibration_sync_failure(self) -> None:
        """Count a rejected CPI only while an empty-room capture is armed."""
        with self._calibration_lock:
            if self._calibration_required_cpis > 0:
                self._calibration_skipped_cpis += 1

    def background_calibration_status(self) -> BackgroundCalibrationStatus:
        with self._calibration_lock:
            required = self._calibration_required_cpis
            collected = self._calibration_collected_cpis
            return BackgroundCalibrationStatus(
                active=required > 0,
                collected_cpis=collected,
                required_cpis=required if required > 0 else collected,
                skipped_cpis=self._calibration_skipped_cpis,
                coherence_restarts=self._calibration_coherence_restarts,
                last_coherence=self._calibration_last_coherence,
                ready=self._calibration is not None,
                error=self._calibration_error,
            )

    def cancel_background_calibration(self, reason: str) -> bool:
        """Discard only the pending candidate while retaining an active background."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("calibration cancellation reason must be nonempty")
        with self._calibration_lock:
            if self._calibration_required_cpis == 0:
                return False
            self._calibration_matrices = []
            self._calibration_required_cpis = 0
            self._calibration_error = reason.strip()
            return True

    def process(self, capture: object) -> RadarFrame:
        started = perf_counter()
        if getattr(capture, "config") != self.config:
            raise ValueError("capture configuration does not match processor configuration")

        known_start_sample = getattr(capture, "chirp_start_sample", 0)
        sync = self._synchronizer.synchronize(
            capture, known_start_sample=known_start_sample
        )
        rx_iq = np.asarray(getattr(capture, "rx_iq"))
        rx_cpi = rx_iq[
            sync.start_sample : sync.start_sample + self.config.cpi_samples
        ]
        chirps = rx_cpi.reshape(
            self.config.chirp_count, self.config.samples_per_chirp
        )[:, : self.config.active_samples].astype(np.complex128, copy=False)
        reference_active = generate_active_chirp(self.config).astype(np.complex128)

        beat = chirps * np.conj(reference_active[None, :])
        beat -= np.mean(beat, axis=1, keepdims=True)
        uncalibrated_beat = beat
        with self._calibration_lock:
            if self._calibration_required_cpis > 0:
                current = beat.astype(np.complex64, copy=True)
                if self._calibration_matrices:
                    _, coherence = BackgroundCalibration._phase_align(
                        self._calibration_matrices[0],
                        current,
                    )
                    self._calibration_last_coherence = coherence
                    if coherence < 0.5:
                        self._calibration_matrices = [current]
                        self._calibration_collected_cpis = 1
                        self._calibration_coherence_restarts += 1
                    else:
                        self._calibration_matrices.append(current)
                        self._calibration_collected_cpis += 1
                else:
                    self._calibration_matrices.append(current)
                    self._calibration_collected_cpis = 1
                if (
                    self._calibration_collected_cpis
                    >= self._calibration_required_cpis
                ):
                    try:
                        candidate = BackgroundCalibration.from_phase_aligned_dechirped(
                            self.config,
                            self._calibration_matrices,
                        )
                    except ValueError as error:
                        self._calibration_error = str(error)
                    else:
                        self._calibration = candidate
                        self._calibration_error = None
                    self._calibration_matrices = []
                    self._calibration_required_cpis = 0
            calibration = self._calibration
        if calibration is not None:
            beat = calibration.subtract_phase_aligned(self.config, beat)
        range_window = np.hanning(self.config.active_samples)
        full_range = np.fft.fft(
            beat * range_window,
            n=self.config.range_fft_size,
            axis=1,
        )
        range_spectrum = np.concatenate(
            (
                full_range[:, :1],
                full_range[:, : self.config.range_fft_size // 2 - 1 : -1],
            ),
            axis=1,
        )
        if calibration is not None and calibration.near_range_guard_m > 0.0:
            uncalibrated_full_range = np.fft.fft(
                uncalibrated_beat * range_window,
                n=self.config.range_fft_size,
                axis=1,
            )
            uncalibrated_range_spectrum = np.concatenate(
                (
                    uncalibrated_full_range[:, :1],
                    uncalibrated_full_range[
                        :, : self.config.range_fft_size // 2 - 1 : -1
                    ],
                ),
                axis=1,
            )
            range_spectrum = calibration.apply_near_range_guard(
                self.config, uncalibrated_range_spectrum, range_spectrum
            )
        adjacent_chirp_correlation = self._adjacent_chirp_correlation(range_spectrum)
        phase_consistency = self._phase_consistency(range_spectrum)
        if self.config.suppress_static_clutter:
            range_spectrum -= np.mean(range_spectrum, axis=0, keepdims=True)

        doppler_window = np.hanning(self.config.chirp_count)
        range_doppler = np.fft.fftshift(
            np.fft.fft(
                range_spectrum * doppler_window[:, None],
                n=self.config.doppler_fft_size,
                axis=0,
            ),
            axes=0,
        )
        coherent_gain = max(
            float(np.sum(range_window) * np.sum(doppler_window)),
            np.finfo(float).tiny,
        )
        magnitude = np.abs(range_doppler) / coherent_gain
        power = magnitude**2
        range_doppler_db = 20.0 * np.log10(
            np.maximum(magnitude, np.finfo(np.float64).tiny)
        )

        range_frequency_hz = (
            np.arange(self.config.range_fft_size // 2 + 1)
            * self.config.sample_rate_hz
            / self.config.range_fft_size
        )
        range_axis_m = (
            range_frequency_hz
            * SPEED_OF_LIGHT_MPS
            / (2.0 * self.config.chirp_slope_hz_per_s)
        )
        doppler_frequency_hz = np.fft.fftshift(
            np.fft.fftfreq(
                self.config.doppler_fft_size,
                d=self.config.chirp_period_s,
            )
        )
        velocity_axis_mps = doppler_frequency_hz * self.config.wavelength_m / 2.0

        rx_magnitude = np.abs(rx_cpi)
        rms_linear = float(np.sqrt(np.mean(rx_magnitude**2)))
        peak_linear = float(np.max(rx_magnitude))
        clip_ratio = float(np.mean(rx_magnitude >= 1.0))
        diagnostics = RadarDiagnostics(
            frame_index=self._frame_index,
            source="iq",
            sync_ok=True,
            sync_score=sync.correlation,
            phase_consistency=phase_consistency,
            rms=self._dbfs(rms_linear),
            peak=self._dbfs(peak_linear),
            clipping=clip_ratio > 0.0,
            clip_ratio=clip_ratio,
            noise_floor_db=float(np.median(range_doppler_db)),
            processing_time_ms=(perf_counter() - started) * 1000.0,
            overruns=0,
        )
        targets = detect_targets(
            power,
            range_axis_m,
            velocity_axis_mps,
            self.config,
            timestamp=float(getattr(capture, "timestamp")),
            sync_score=sync.correlation,
            adjacent_chirp_correlation=adjacent_chirp_correlation,
            phase_consistency=phase_consistency,
            velocity_trust_threshold=self._velocity_trust_threshold,
        )
        frame = RadarFrame(
            frame_index=self._frame_index,
            timestamp=float(getattr(capture, "timestamp")),
            config_snapshot=self.config,
            range_axis_m=range_axis_m,
            velocity_axis_mps=velocity_axis_mps,
            range_doppler_db=range_doppler_db,
            targets=targets,
            diagnostics=diagnostics,
        )
        self._frame_index += 1
        return frame

    @staticmethod
    def _dbfs(value: float) -> float:
        return float(20.0 * np.log10(max(value, np.finfo(float).tiny)))

    @staticmethod
    def _phase_consistency(range_spectrum: np.ndarray) -> float:
        if range_spectrum.shape[0] < 2 or range_spectrum.shape[1] < 2:
            return 0.0
        strongest_bin = 1 + int(
            np.argmax(np.mean(np.abs(range_spectrum[:, 1:]) ** 2, axis=0))
        )
        slow_time = range_spectrum[:, strongest_bin]
        increments = slow_time[1:] * np.conj(slow_time[:-1])
        valid = np.abs(increments) > np.finfo(float).tiny
        if not np.any(valid):
            return 0.0
        unit_increments = increments[valid] / np.abs(increments[valid])
        return float(np.clip(np.abs(np.mean(unit_increments)), 0.0, 1.0))

    @staticmethod
    def _adjacent_chirp_correlation(range_spectrum: np.ndarray) -> float:
        if range_spectrum.shape[0] < 2:
            return 0.0
        first = range_spectrum[:-1]
        second = range_spectrum[1:]
        numerator = np.abs(np.sum(second * np.conj(first), axis=1))
        denominator = np.linalg.norm(first, axis=1) * np.linalg.norm(second, axis=1)
        valid = denominator > np.finfo(float).tiny
        if not np.any(valid):
            return 0.0
        correlations = numerator[valid] / denominator[valid]
        return float(np.clip(np.mean(correlations), 0.0, 1.0))
