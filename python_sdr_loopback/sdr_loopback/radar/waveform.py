"""Reference complex-baseband FMCW waveform generation."""

import numpy as np

from .config import RadarConfig


def generate_active_chirp(config: RadarConfig) -> np.ndarray:
    """Generate one unit-magnitude active FMCW chirp."""
    time_s = np.arange(config.active_samples, dtype=np.float64) / config.sample_rate_hz
    start_hz = -0.5 * config.bandwidth_hz
    phase = 2.0 * np.pi * (
        start_hz * time_s + 0.5 * config.chirp_slope_hz_per_s * time_s**2
    )
    return np.exp(1j * phase).astype(np.complex64)


def generate_chirp(config: RadarConfig) -> np.ndarray:
    """Generate one active FMCW chirp followed by its exact-zero idle interval."""
    chirp = np.zeros(config.samples_per_chirp, dtype=np.complex64)
    chirp[: config.active_samples] = generate_active_chirp(config)
    return chirp


def generate_cpi(config: RadarConfig) -> np.ndarray:
    """Generate one coherent processing interval of identical FMCW chirps."""
    return np.tile(generate_chirp(config), config.chirp_count)
