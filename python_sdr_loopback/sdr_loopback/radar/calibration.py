"""Empty-room background calibration for aligned FMCW dechirped CPIs."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .synchronizer import ChirpSynchronizer
from .waveform import generate_active_chirp


@dataclass(frozen=True)
class BackgroundCalibration:
    config_hash: str
    mean_dechirped: np.ndarray
    cpi_count: int
    near_range_guard_m: float = 0.0

    def __post_init__(self) -> None:
        mean = np.asarray(self.mean_dechirped)
        valid_hash = (
            isinstance(self.config_hash, str)
            and len(self.config_hash) == 64
            and all(character in "0123456789abcdef" for character in self.config_hash)
        )
        if not valid_hash:
            raise ValueError("calibration config_hash must be a SHA-256 hex digest")
        if mean.ndim != 2 or not np.iscomplexobj(mean) or not np.all(np.isfinite(mean)):
            raise ValueError("calibration mean_dechirped must be a finite complex matrix")
        if (
            not isinstance(self.cpi_count, (int, np.integer))
            or isinstance(self.cpi_count, (bool, np.bool_))
            or self.cpi_count <= 0
        ):
            raise ValueError("calibration cpi_count must be a positive integer")
        if not np.isfinite(self.near_range_guard_m) or self.near_range_guard_m < 0.0:
            raise ValueError("calibration near_range_guard_m must be nonnegative and finite")
        object.__setattr__(self, "mean_dechirped", mean.astype(np.complex64, copy=False))
        object.__setattr__(self, "cpi_count", int(self.cpi_count))
        object.__setattr__(self, "near_range_guard_m", float(self.near_range_guard_m))

    @staticmethod
    def hash_config(config: RadarConfig) -> str:
        encoded = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dechirped(
        cls,
        config: RadarConfig,
        matrices: object,
        *,
        near_range_guard_m: float = 0.0,
    ) -> "BackgroundCalibration":
        expected_tail = (config.chirp_count, config.active_samples)
        if not np.isfinite(near_range_guard_m) or near_range_guard_m < 0.0:
            raise ValueError("near_range_guard_m must be nonnegative and finite")
        total = np.zeros(expected_tail, dtype=np.complex128)
        count = 0
        for matrix in matrices:
            values = np.asarray(matrix, dtype=np.complex128)
            if values.shape != expected_tail:
                raise ValueError(
                    f"dechirped CPIs must have shape (N, {expected_tail[0]}, {expected_tail[1]})"
                )
            if not np.all(np.isfinite(values)):
                raise ValueError("dechirped CPIs must be finite")
            total += values
            count += 1
        if count == 0:
            raise ValueError(
                f"dechirped CPIs must have shape (N, {expected_tail[0]}, {expected_tail[1]})"
            )
        return cls(
            cls.hash_config(config),
            (total / count).astype(np.complex64),
            count,
            float(near_range_guard_m),
        )

    @classmethod
    def from_phase_aligned_dechirped(
        cls,
        config: RadarConfig,
        matrices: object,
        *,
        minimum_coherence: float = 0.5,
        near_range_guard_m: float = 0.0,
    ) -> "BackgroundCalibration":
        """Build a coherent mean after removing one common phase per CPI."""
        if not np.isfinite(minimum_coherence) or not 0.0 <= minimum_coherence <= 1.0:
            raise ValueError("minimum_coherence must be finite and in [0, 1]")
        values = [np.asarray(matrix, dtype=np.complex128) for matrix in matrices]
        if not values:
            return cls.from_dechirped(
                config, values, near_range_guard_m=near_range_guard_m
            )
        reference = values[0]
        aligned = []
        for matrix in values:
            corrected, coherence = cls._phase_align(reference, matrix)
            if coherence < minimum_coherence:
                raise ValueError(
                    "background CPI coherence is below the calibration threshold"
                )
            aligned.append(corrected)
        return cls.from_dechirped(
            config,
            aligned,
            near_range_guard_m=near_range_guard_m,
        )

    @classmethod
    def from_captures(
        cls,
        config: RadarConfig,
        captures: object,
        *,
        near_range_guard_m: float = 0.0,
    ) -> "BackgroundCalibration":
        reference = generate_active_chirp(config).astype(np.complex128)
        synchronizer = ChirpSynchronizer(config, mode="correlation")
        def matrices():
            for capture in captures:
                if getattr(capture, "config") != config:
                    raise ValueError(
                        "capture configuration does not match calibration configuration"
                    )
                start = synchronizer.synchronize(capture).start_sample
                rx = np.asarray(getattr(capture, "rx_iq"))[
                    start : start + config.cpi_samples
                ]
                if rx.size != config.cpi_samples or not np.all(np.isfinite(rx)):
                    raise ValueError(
                        "calibration capture must contain one finite aligned CPI"
                    )
                chirps = rx.reshape(config.chirp_count, config.samples_per_chirp)[
                    :, : config.active_samples
                ]
                beat = chirps * np.conj(reference[None, :])
                beat -= np.mean(beat, axis=1, keepdims=True)
                yield beat
        return cls.from_dechirped(
            config, matrices(), near_range_guard_m=near_range_guard_m
        )

    def _validate_config(self, config: RadarConfig) -> None:
        if self.config_hash != self.hash_config(config):
            raise ValueError("calibration configuration does not match radar configuration")
        expected = (config.chirp_count, config.active_samples)
        if self.mean_dechirped.shape != expected:
            raise ValueError(
                f"calibration mean_dechirped must have shape {expected} for configuration"
            )

    def subtract(self, config: RadarConfig, dechirped: np.ndarray) -> np.ndarray:
        self._validate_config(config)
        values = np.asarray(dechirped)
        if values.shape != self.mean_dechirped.shape:
            raise ValueError("dechirped matrix shape does not match calibration")
        return values - self.mean_dechirped

    def subtract_phase_aligned(
        self,
        config: RadarConfig,
        dechirped: np.ndarray,
        *,
        minimum_coherence: float = 0.5,
    ) -> np.ndarray:
        """Align one CPI to the stored background before complex subtraction."""
        self._validate_config(config)
        values = np.asarray(dechirped)
        if values.shape != self.mean_dechirped.shape:
            raise ValueError("dechirped matrix shape does not match calibration")
        aligned, coherence = self._phase_align(self.mean_dechirped, values)
        if coherence < minimum_coherence:
            return values
        return aligned - self.mean_dechirped

    @staticmethod
    def _phase_align(
        reference: np.ndarray,
        values: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        if reference.shape != values.shape or reference.ndim != 2:
            raise ValueError("background CPIs must have identical two-dimensional shapes")
        if not np.all(np.isfinite(reference)) or not np.all(np.isfinite(values)):
            raise ValueError("background CPIs must be finite")
        reference_norm = float(np.linalg.norm(reference.ravel()))
        values_norm = float(np.linalg.norm(values.ravel()))
        denominator = reference_norm * values_norm
        if not np.isfinite(denominator) or denominator <= np.finfo(float).tiny:
            raise ValueError("background CPI coherence requires nonzero finite energy")
        correlation = np.vdot(reference.ravel(), values.ravel())
        coherence = float(np.abs(correlation) / denominator)
        if not np.isfinite(coherence):
            raise ValueError("background CPI coherence must be finite")
        aligned = values * np.exp(-1j * np.angle(correlation))
        return aligned, float(np.clip(coherence, 0.0, 1.0))

    def near_range_guard_bins(self, config: RadarConfig) -> int:
        self._validate_config(config)
        bin_spacing_hz = config.sample_rate_hz / config.range_fft_size
        guard_frequency_hz = (
            self.near_range_guard_m * 2.0 * config.chirp_slope_hz_per_s / SPEED_OF_LIGHT_MPS
        )
        return min(config.range_fft_size // 2 + 1, int(np.ceil(guard_frequency_hz / bin_spacing_hz)))

    def apply_near_range_guard(
        self,
        config: RadarConfig,
        original: np.ndarray,
        calibrated: np.ndarray,
    ) -> np.ndarray:
        guard_bins = self.near_range_guard_bins(config)
        result = np.asarray(calibrated).copy()
        result[:, :guard_bins] = np.asarray(original)[:, :guard_bins]
        return result

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path,
            config_hash=self.config_hash,
            mean_dechirped=self.mean_dechirped,
            cpi_count=self.cpi_count,
            near_range_guard_m=self.near_range_guard_m,
        )

    @classmethod
    def load(cls, path: str | Path) -> "BackgroundCalibration":
        try:
            with np.load(path, allow_pickle=False) as archive:
                return cls(
                    str(archive["config_hash"].item()),
                    np.asarray(archive["mean_dechirped"]),
                    archive["cpi_count"].item(),
                    archive["near_range_guard_m"].item(),
                )
        except (KeyError, TypeError) as error:
            raise ValueError("calibration archive metadata is incomplete") from error
