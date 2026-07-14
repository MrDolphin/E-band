"""Immutable FMCW radar configuration and derived physical dimensions."""

from dataclasses import dataclass
import math


SPEED_OF_LIGHT_MPS = 299_792_458.0


@dataclass(frozen=True)
class RadarConfig:
    carrier_hz: float = 76e9
    sample_rate_hz: float = 30e6
    bandwidth_hz: float = 20e6
    active_time_s: float = 128e-6
    idle_time_s: float = 16e-6
    chirp_count: int = 64
    range_fft_size: int = 4096
    doppler_fft_size: int = 64
    max_display_range_m: float = 50.0
    cfar_threshold_db: float = 12.0

    def __post_init__(self) -> None:
        active = self.sample_rate_hz * self.active_time_s
        total = self.sample_rate_hz * self.chirp_period_s
        if not math.isclose(active, round(active), abs_tol=1e-9):
            raise ValueError("active_time_s must produce an integer sample count")
        if not math.isclose(total, round(total), abs_tol=1e-9):
            raise ValueError("chirp period must produce an integer sample count")
        if not self._is_positive_power_of_two(self.range_fft_size):
            raise ValueError("range_fft_size must be a positive power of two")
        if not self._is_positive_power_of_two(self.doppler_fft_size):
            raise ValueError("doppler_fft_size must be a positive power of two")
        if self.range_fft_size < round(active):
            raise ValueError("range_fft_size must cover all active samples")
        if self.doppler_fft_size < self.chirp_count:
            raise ValueError("doppler_fft_size must be at least chirp_count")

    @staticmethod
    def _is_positive_power_of_two(value: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            and value & (value - 1) == 0
        )

    @property
    def chirp_period_s(self) -> float:
        return self.active_time_s + self.idle_time_s

    @property
    def active_samples(self) -> int:
        return round(self.sample_rate_hz * self.active_time_s)

    @property
    def samples_per_chirp(self) -> int:
        return round(self.sample_rate_hz * self.chirp_period_s)

    @property
    def cpi_samples(self) -> int:
        return self.samples_per_chirp * self.chirp_count

    @property
    def wavelength_m(self) -> float:
        return SPEED_OF_LIGHT_MPS / self.carrier_hz

    @property
    def range_resolution_m(self) -> float:
        return SPEED_OF_LIGHT_MPS / (2.0 * self.bandwidth_hz)

    @property
    def chirp_slope_hz_per_s(self) -> float:
        return self.bandwidth_hz / self.active_time_s

    @property
    def max_unambiguous_velocity_mps(self) -> float:
        return self.wavelength_m / (4.0 * self.chirp_period_s)

    @property
    def velocity_resolution_mps(self) -> float:
        return (2.0 * self.max_unambiguous_velocity_mps) / self.doppler_fft_size
