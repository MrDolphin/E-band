import unittest

import numpy as np

from sdr_loopback.radar.config import RadarConfig, SPEED_OF_LIGHT_MPS
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.simulator import simulate_capture


def small_config() -> RadarConfig:
    return RadarConfig(
        carrier_hz=24e9,
        sample_rate_hz=1e6,
        bandwidth_hz=200e3,
        active_time_s=64e-6,
        idle_time_s=16e-6,
        chirp_count=32,
        range_fft_size=64,
        doppler_fft_size=32,
    )


class RadarSimulatorTests(unittest.TestCase):
    def test_fractional_delay_evaluates_the_chirp_without_sample_rounding(self):
        config = small_config()
        target = SyntheticTarget(
            "T01", range_m=22.5, radial_velocity_mps=0.0, snr_db=float("inf")
        )

        capture = simulate_capture(config, (target,), seed=1)
        delay_s = 2.0 * target.range_m / SPEED_OF_LIGHT_MPS
        sample_time_s = 10.0 / config.sample_rate_hz
        delayed_time_s = sample_time_s - delay_s
        expected = np.exp(
            1j
            * 2.0
            * np.pi
            * (
                -0.5 * config.bandwidth_hz * delayed_time_s
                + 0.5 * config.chirp_slope_hz_per_s * delayed_time_s**2
            )
        )

        self.assertFalse(np.isclose(delay_s * config.sample_rate_hz, round(delay_s * config.sample_rate_hz)))
        np.testing.assert_allclose(capture.rx_iq[10], expected, atol=1e-6)

    def test_positive_velocity_has_positive_slow_time_phase_direction(self):
        config = small_config()
        target = SyntheticTarget(
            "T01", range_m=22.5, radial_velocity_mps=1.0, snr_db=float("inf")
        )

        capture = simulate_capture(config, (target,), seed=1)
        sample_index = 10
        first = capture.rx_iq[sample_index]
        second = capture.rx_iq[config.samples_per_chirp + sample_index]
        expected_phase = 2.0 * np.pi * target.doppler_hz(config) * config.chirp_period_s

        self.assertGreater(np.angle(second * np.conj(first)), 0.0)
        self.assertAlmostEqual(np.angle(second * np.conj(first)), expected_phase, places=6)

    def test_identical_seed_produces_identical_iq_and_truth_stays_on_capture(self):
        config = small_config()
        target = SyntheticTarget("T01", range_m=22.5, radial_velocity_mps=0.0, snr_db=12.0)

        first = simulate_capture(config, (target,), seed=1234)
        second = simulate_capture(config, (target,), seed=1234)

        np.testing.assert_array_equal(first.tx_iq, second.tx_iq)
        np.testing.assert_array_equal(first.rx_iq, second.rx_iq)
        self.assertEqual(first.truth_targets, (target,))
        self.assertFalse(hasattr(first.rx_iq, "truth_targets"))

    def test_leakage_and_static_clutter_are_added_to_the_receive_iq(self):
        config = small_config()

        capture = simulate_capture(
            config,
            (),
            leakage_amplitude=0.25,
            clutter_amplitude=0.5,
            clutter_range_m=22.5,
            seed=1,
        )

        self.assertAlmostEqual(capture.rx_iq[0], 0.25 + 0.0j, places=6)
        self.assertGreater(np.abs(capture.rx_iq[10]), 0.25)
        self.assertEqual(capture.truth_targets, ())

    def test_multiple_targets_sum_and_noise_rms_follows_per_target_snr(self):
        config = small_config()
        targets = (
            SyntheticTarget("T01", range_m=22.5, radial_velocity_mps=0.0, amplitude=0.8, snr_db=10.0),
            SyntheticTarget("T02", range_m=37.5, radial_velocity_mps=0.5, amplitude=0.4, snr_db=20.0),
        )
        noiseless = [
            simulate_capture(
                config,
                (SyntheticTarget(**{**target.__dict__, "snr_db": float("inf")}),),
                seed=1,
            )
            for target in targets
        ]
        combined_noiseless = simulate_capture(
            config,
            tuple(SyntheticTarget(**{**target.__dict__, "snr_db": float("inf")}) for target in targets),
            seed=1,
        )
        capture = simulate_capture(config, targets, seed=99)
        expected_noise_rms = np.sqrt(
            sum(
                np.mean(np.abs(single.rx_iq) ** 2) * 10.0 ** (-target.snr_db / 10.0)
                for single, target in zip(noiseless, targets)
            )
        )
        noise = capture.rx_iq - sum(single.rx_iq for single in noiseless)

        np.testing.assert_allclose(
            combined_noiseless.rx_iq,
            sum(single.rx_iq for single in noiseless),
            atol=1e-6,
        )
        self.assertAlmostEqual(np.sqrt(np.mean(np.abs(noise) ** 2)), expected_noise_rms, delta=0.08 * expected_noise_rms)


if __name__ == "__main__":
    unittest.main()
