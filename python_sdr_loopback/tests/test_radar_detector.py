import unittest
from types import SimpleNamespace

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.detector import ca_cfar_2d, detect_targets
from sdr_loopback.radar.models import RadarCapture, SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.simulator import simulate_capture
from sdr_loopback.radar.waveform import generate_cpi


def reduced_config(**overrides) -> RadarConfig:
    values = {
        "chirp_count": 32,
        "doppler_fft_size": 32,
    }
    values.update(overrides)
    return RadarConfig(**values)


class CfarTests(unittest.TestCase):
    def test_near_dc_range_bin_uses_one_sided_training(self):
        config = RadarConfig(cfar_threshold_db=12.0)
        shape = (config.doppler_fft_size, config.range_fft_size // 2 + 1)
        power = np.ones(shape, dtype=float)
        doppler_bin = config.doppler_fft_size // 2
        range_bin = 3
        power[doppler_bin, range_bin] = 100.0

        mask, noise_power = ca_cfar_2d(power, config)

        self.assertLess(range_bin, 8 + 2)
        self.assertTrue(mask[doppler_bin, range_bin])
        self.assertAlmostEqual(noise_power[doppler_bin, range_bin], 1.0)
        self.assertFalse(np.any(mask[:5]))
        self.assertFalse(np.any(mask[-5:]))

    def test_controlled_power_fixture_reports_local_snr_and_deterministic_ids(self):
        config = reduced_config(cfar_threshold_db=6.0, max_display_range_m=200.0)
        shape = (config.doppler_fft_size, config.range_fft_size // 2 + 1)
        power = np.ones(shape, dtype=float)
        power[16, 3] = 100.0
        power[21, 20] = 25.0
        ranges = np.arange(shape[1], dtype=float) * 7.5
        velocities = (np.arange(shape[0], dtype=float) - 16.0) * 0.25
        diagnostics = SimpleNamespace(sync_score=1.0, phase_consistency=1.0)

        targets = detect_targets(
            power,
            ranges,
            velocities,
            config,
            timestamp=4.5,
            diagnostics=diagnostics,
        )

        self.assertEqual([target.target_id for target in targets], ["T01", "T02"])
        self.assertAlmostEqual(targets[0].snr_db, 20.0, delta=3.0)
        self.assertGreater(targets[0].snr_db, targets[1].snr_db)
        self.assertEqual(targets[0].range_bin, 3)
        self.assertEqual(targets[0].doppler_bin, 16)
        self.assertTrue(all(target.azimuth_deg is None for target in targets))
        self.assertTrue(all(0.0 <= target.confidence <= 1.0 for target in targets))


class ProcessorDetectionTests(unittest.TestCase):
    def assert_target_matches(self, target, truth, config):
        self.assertLessEqual(
            abs(target.range_m - truth.range_m), config.range_resolution_m
        )
        self.assertLessEqual(
            abs(target.radial_velocity_mps - truth.radial_velocity_mps),
            config.velocity_resolution_mps,
        )
        self.assertIsNone(target.azimuth_deg)

    def process_targets(self, config, targets, *, seed=1, **capture_options):
        capture = simulate_capture(
            config,
            targets,
            timestamp=12.5,
            seed=seed,
            **capture_options,
        )
        return FmcwProcessor(config).process(capture)

    def test_default_physics_detects_stationary_target_at_22_5_m(self):
        config = RadarConfig()
        truth = SyntheticTarget("input-id", 22.5, 0.0, amplitude=0.5, snr_db=30.0)

        frame = self.process_targets(config, (truth,), seed=7)

        self.assertGreaterEqual(len(frame.targets), 1)
        self.assert_target_matches(frame.targets[0], truth, config)
        self.assertEqual(frame.targets[0].target_id, "T01")
        self.assertEqual(frame.targets[0].timestamp, frame.timestamp)
        self.assertEqual(frame.frame_index, frame.diagnostics.frame_index)

    def test_moving_single_target_is_detected(self):
        config = reduced_config()
        truth = SyntheticTarget("truth", 37.5, 1.2, amplitude=0.4, snr_db=24.0)

        frame = self.process_targets(config, (truth,), seed=8)

        self.assertGreaterEqual(len(frame.targets), 1)
        self.assert_target_matches(frame.targets[0], truth, config)

    def test_two_separated_targets_are_detected(self):
        config = reduced_config()
        truth = (
            SyntheticTarget("A", 15.0, -1.7, amplitude=0.6, snr_db=28.0),
            SyntheticTarget("B", 45.0, 2.1, amplitude=0.35, snr_db=25.0),
        )

        frame = self.process_targets(config, truth, seed=9)

        for expected in truth:
            self.assertTrue(
                any(
                    abs(target.range_m - expected.range_m)
                    <= config.range_resolution_m
                    and abs(target.radial_velocity_mps - expected.radial_velocity_mps)
                    <= config.velocity_resolution_mps
                    for target in frame.targets
                )
            )
        self.assertEqual(
            [target.target_id for target in frame.targets],
            [f"T{index:02d}" for index in range(1, len(frame.targets) + 1)],
        )

    def test_same_range_different_velocity_targets_stay_separate(self):
        config = reduced_config()
        truth = (
            SyntheticTarget("A", 30.0, -2.0, amplitude=0.5, snr_db=28.0),
            SyntheticTarget("B", 30.0, 2.0, amplitude=0.5, snr_db=28.0),
        )

        frame = self.process_targets(config, truth, seed=10)

        matches = [
            target
            for target in frame.targets
            if abs(target.range_m - 30.0) <= config.range_resolution_m
        ]
        self.assertTrue(
            any(
                abs(target.radial_velocity_mps + 2.0)
                <= config.velocity_resolution_mps
                for target in matches
            )
        )
        self.assertTrue(
            any(
                abs(target.radial_velocity_mps - 2.0)
                <= config.velocity_resolution_mps
                for target in matches
            )
        )

    def test_weak_target_above_cfar_margin_is_detected(self):
        config = reduced_config(cfar_threshold_db=9.0)
        truth = SyntheticTarget("weak", 30.0, 1.0, amplitude=0.08, snr_db=13.0)

        frame = self.process_targets(config, (truth,), seed=11)

        self.assertTrue(
            all(target.range_m <= config.max_display_range_m for target in frame.targets)
        )
        self.assertTrue(
            any(
                abs(target.range_m - truth.range_m) <= config.range_resolution_m
                and abs(target.radial_velocity_mps - truth.radial_velocity_mps)
                <= config.velocity_resolution_mps
                for target in frame.targets
            )
        )

    def test_ten_seeded_noise_cpis_have_no_persistent_detection(self):
        config = reduced_config()
        tx_iq = generate_cpi(config)
        detected_cells = []
        for seed in range(10):
            generator = np.random.default_rng(seed)
            noise = generator.normal(size=config.cpi_samples) + 1j * generator.normal(
                size=config.cpi_samples
            )
            capture = RadarCapture(
                timestamp=float(seed),
                config=config,
                tx_iq=tx_iq,
                rx_iq=(0.02 * noise / np.sqrt(2.0)).astype(np.complex64),
            )
            frame = FmcwProcessor(config).process(capture)
            detected_cells.append(
                {(target.doppler_bin, target.range_bin) for target in frame.targets}
            )

        persistent = set.intersection(*detected_cells) if detected_cells else set()
        self.assertEqual(persistent, set())

    def test_zero_range_leakage_does_not_hide_distant_target(self):
        config = reduced_config()
        truth = SyntheticTarget("distant", 45.0, 1.5, amplitude=0.25, snr_db=25.0)

        frame = self.process_targets(
            config, (truth,), seed=12, leakage_amplitude=4.0
        )

        self.assertTrue(
            any(
                abs(target.range_m - truth.range_m) <= config.range_resolution_m
                and abs(target.radial_velocity_mps - truth.radial_velocity_mps)
                <= config.velocity_resolution_mps
                for target in frame.targets
            )
        )

    def test_static_clutter_suppression_is_opt_in(self):
        truth = SyntheticTarget("stationary", 22.5, 0.0, amplitude=0.5, snr_db=30.0)
        default_config = reduced_config()
        suppressed_config = reduced_config(suppress_static_clutter=True)

        visible = self.process_targets(default_config, (truth,), seed=13)
        suppressed = self.process_targets(suppressed_config, (truth,), seed=13)

        self.assertFalse(default_config.suppress_static_clutter)
        self.assertTrue(
            any(
                abs(target.range_m - truth.range_m)
                <= default_config.range_resolution_m
                for target in visible.targets
            )
        )
        self.assertFalse(
            any(
                abs(target.range_m - truth.range_m)
                <= suppressed_config.range_resolution_m
                and abs(target.radial_velocity_mps)
                <= suppressed_config.velocity_resolution_mps
                for target in suppressed.targets
            )
        )

    def test_processor_never_reads_truth_targets(self):
        config = reduced_config()
        simulated = simulate_capture(
            config,
            (SyntheticTarget("hidden", 30.0, 1.0, snr_db=25.0),),
            seed=14,
        )

        class CaptureWithoutTruth:
            timestamp = simulated.timestamp
            config = simulated.config
            tx_iq = simulated.tx_iq
            rx_iq = simulated.rx_iq

            @property
            def truth_targets(self):
                raise AssertionError("processor read truth_targets")

        frame = FmcwProcessor(config).process(CaptureWithoutTruth())

        self.assertGreaterEqual(len(frame.targets), 1)


if __name__ == "__main__":
    unittest.main()
