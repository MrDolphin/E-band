"""Shared NumPy range-Doppler processing for FMCW receive IQ."""

from time import perf_counter

import numpy as np

from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .models import RadarDiagnostics, RadarFrame
from .synchronizer import ChirpSynchronizer
from .waveform import generate_active_chirp


class FmcwProcessor:
    """Convert one synchronized time-domain CPI into a range-Doppler frame."""

    def __init__(self, config: RadarConfig, *, sync_mode: str = "known") -> None:
        self.config = config
        self._synchronizer = ChirpSynchronizer(config, mode=sync_mode)
        self._frame_index = 0

    def process(self, capture: object) -> RadarFrame:
        started = perf_counter()
        if getattr(capture, "config") != self.config:
            raise ValueError("capture configuration does not match processor configuration")

        sync = self._synchronizer.synchronize(capture)
        rx_iq = np.asarray(getattr(capture, "rx_iq"))
        chirps = np.stack(
            [
                rx_iq[start : start + self.config.active_samples]
                for start in sync.chirp_starts
            ]
        ).astype(np.complex128, copy=False)
        reference_active = generate_active_chirp(self.config).astype(np.complex128)

        beat = chirps * np.conj(reference_active[None, :])
        beat -= np.mean(beat, axis=1, keepdims=True)
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
        phase_consistency = self._phase_consistency(range_spectrum)
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

        rx_magnitude = np.abs(rx_iq)
        rms_linear = float(np.sqrt(np.mean(rx_magnitude**2)))
        peak_linear = float(np.max(rx_magnitude))
        clip_ratio = float(np.mean(rx_magnitude >= 1.0))
        diagnostics = RadarDiagnostics(
            frame_index=self._frame_index,
            source="iq",
            sync_ok=True,
            phase_consistency=phase_consistency,
            rms=self._dbfs(rms_linear),
            peak=self._dbfs(peak_linear),
            clipping=clip_ratio > 0.0,
            noise_floor_db=float(np.median(range_doppler_db)),
            processing_time_ms=(perf_counter() - started) * 1000.0,
            overruns=0,
        )
        frame = RadarFrame(
            frame_index=self._frame_index,
            timestamp=float(getattr(capture, "timestamp")),
            config_snapshot=self.config,
            range_axis_m=range_axis_m,
            velocity_axis_mps=velocity_axis_mps,
            range_doppler_db=range_doppler_db,
            targets=(),
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
