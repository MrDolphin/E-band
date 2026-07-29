"""Time-domain synthetic FMCW receive-IQ generation.

Each ``SyntheticTarget.amplitude`` is used directly as its complex echo voltage
coefficient.  The simulator models target echo power over the full CPI, then
sets the circular-complex noise RMS to::

    sqrt(sum(mean(abs(echo_k)**2) * 10**(-target_k.snr_db / 10)))

where ``echo_k`` is the target-only time-domain echo.  Thus every target
contributes noise power implied by its own requested SNR; leakage and clutter
do not affect the calculated noise level.  The RMS is deterministic for a
given configuration and target set, while ``seed`` controls the sampled noise.
"""

from collections.abc import Sequence

import numpy as np

from .config import RadarConfig, SPEED_OF_LIGHT_MPS
from .models import RadarCapture, SyntheticTarget
from .waveform import generate_cpi


def _chirp_at(time_s: np.ndarray, config: RadarConfig) -> np.ndarray:
    start_hz = -0.5 * config.bandwidth_hz
    phase = 2.0 * np.pi * (
        start_hz * time_s + 0.5 * config.chirp_slope_hz_per_s * time_s**2
    )
    return np.exp(1j * phase)


def _target_echo(
    config: RadarConfig,
    amplitude: float,
    range_m: float,
    doppler_hz: float,
) -> np.ndarray:
    sample_index = np.arange(config.cpi_samples, dtype=np.int64)
    fast_time_s = (sample_index % config.samples_per_chirp) / config.sample_rate_hz
    absolute_time_s = sample_index / config.sample_rate_hz
    delayed_time_s = fast_time_s - 2.0 * range_m / SPEED_OF_LIGHT_MPS
    valid = (delayed_time_s >= 0.0) & (delayed_time_s < config.active_time_s)

    echo = np.zeros(config.cpi_samples, dtype=np.complex128)
    echo[valid] = _chirp_at(delayed_time_s[valid], config)
    echo *= amplitude * np.exp(1j * 2.0 * np.pi * doppler_hz * absolute_time_s)
    return echo


def simulate_capture(
    config: RadarConfig,
    targets: Sequence[SyntheticTarget] = (),
    *,
    timestamp: float = 0.0,
    seed: int | None = None,
    leakage_amplitude: float = 0.0,
    clutter_amplitude: float = 0.0,
    clutter_range_m: float = 0.0,
) -> RadarCapture:
    """Generate one reference transmit CPI and its synthetic complex receive IQ."""
    truth_targets = tuple(targets)
    tx_iq = generate_cpi(config)
    target_echoes = [
        _target_echo(
            config,
            target.amplitude,
            target.range_m,
            target.doppler_hz(config),
        )
        for target in truth_targets
    ]
    rx_iq = np.zeros(config.cpi_samples, dtype=np.complex128)
    for echo in target_echoes:
        rx_iq += echo

    rx_iq += leakage_amplitude * tx_iq
    if clutter_amplitude:
        rx_iq += _target_echo(config, clutter_amplitude, clutter_range_m, 0.0)

    noise_power = sum(
        float(np.mean(np.abs(echo) ** 2)) * 10.0 ** (-target.snr_db / 10.0)
        for echo, target in zip(target_echoes, truth_targets)
    )
    if noise_power:
        noise_rms = np.sqrt(noise_power)
        generator = np.random.default_rng(seed)
        noise = generator.normal(size=config.cpi_samples) + 1j * generator.normal(
            size=config.cpi_samples
        )
        rx_iq += noise_rms * noise / np.sqrt(2.0)

    return RadarCapture(
        timestamp=timestamp,
        config=config,
        tx_iq=tx_iq,
        rx_iq=rx_iq.astype(np.complex64),
        truth_targets=truth_targets,
    )
