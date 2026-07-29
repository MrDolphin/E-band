import unittest

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.waveform import (
    generate_active_chirp,
    generate_chirp,
    generate_cpi,
)


class RadarWaveformTests(unittest.TestCase):
    def test_active_chirp_has_configured_unit_magnitude_samples(self):
        chirp = generate_active_chirp(RadarConfig())

        self.assertEqual(chirp.shape, (3840,))
        self.assertEqual(chirp.dtype, np.complex64)
        np.testing.assert_allclose(np.abs(chirp), 1.0, atol=1e-6)

    def test_chirp_appends_an_exact_zero_idle_region(self):
        config = RadarConfig()
        chirp = generate_chirp(config)

        self.assertEqual(chirp.shape, (4320,))
        np.testing.assert_allclose(np.abs(chirp[: config.active_samples]), 1.0, atol=1e-6)
        np.testing.assert_array_equal(chirp[config.active_samples :], 0.0)

    def test_cpi_tiles_exactly_the_configured_chirp_count(self):
        config = RadarConfig()
        cpi = generate_cpi(config)

        self.assertEqual(cpi.shape, (276480,))
        np.testing.assert_array_equal(cpi[: config.samples_per_chirp], generate_chirp(config))
        np.testing.assert_array_equal(cpi[-config.samples_per_chirp :], generate_chirp(config))

    def test_active_chirp_sweeps_the_configured_bandwidth_within_nyquist(self):
        config = RadarConfig()
        phase = np.unwrap(np.angle(generate_active_chirp(config))).astype(np.float64)
        instantaneous_hz = np.diff(phase) * config.sample_rate_hz / (2.0 * np.pi)

        self.assertGreaterEqual(instantaneous_hz.min(), -0.5 * config.bandwidth_hz - 2e3)
        self.assertLessEqual(instantaneous_hz.max(), 0.5 * config.bandwidth_hz + 2e3)
        self.assertAlmostEqual(
            np.ptp(instantaneous_hz),
            config.bandwidth_hz * (1.0 - 1.0 / config.active_samples),
            delta=6e3,
        )
        self.assertLess(np.abs(instantaneous_hz).max(), 0.5 * config.sample_rate_hz)


if __name__ == "__main__":
    unittest.main()
