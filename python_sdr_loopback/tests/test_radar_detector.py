import unittest
from types import SimpleNamespace

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.detector import (
    ca_cfar_2d,
    cfar_training_stats,
    cluster_detections,
    detect_targets,
    local_peak_mask,
)
from sdr_loopback.radar.models import (
    RadarCapture,
    RadarDiagnostics,
    RadarFrame,
    RadarTarget,
    SyntheticTarget,
)
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
    def test_default_config_allows_first_two_non_dc_range_bins(self):
        config = RadarConfig(cfar_threshold_db=12.0)
        shape = (config.doppler_fft_size, config.range_fft_size // 2 + 1)
        doppler_bin = config.doppler_fft_size // 2

        for range_bin in (1, 2):
            with self.subTest(range_bin=range_bin):
                power = np.ones(shape, dtype=float)
                power[doppler_bin, range_bin] = 100.0

                mask, noise_power = ca_cfar_2d(power, config)

                self.assertTrue(mask[doppler_bin, range_bin])
                self.assertAlmostEqual(noise_power[doppler_bin, range_bin], 1.0)
        power = np.ones(shape, dtype=float)
        power[doppler_bin, 0] = 100.0
        mask, noise_power = ca_cfar_2d(power, config)
        self.assertFalse(mask[doppler_bin, 0])
        self.assertTrue(np.isnan(noise_power[doppler_bin, 0]))

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

    def test_integral_training_stats_match_brute_force_sum_and_count(self):
        generator = np.random.default_rng(123)
        power = generator.uniform(0.1, 3.0, size=(16, 24))
        doppler_training = 2
        doppler_guard = 1
        range_training = 3
        range_guard = 1
        sums, counts = cfar_training_stats(
            power,
            doppler_training=doppler_training,
            doppler_guard=doppler_guard,
            range_training=range_training,
            range_guard=range_guard,
        )
        expected_sums = np.zeros_like(power)
        expected_counts = np.zeros(power.shape, dtype=int)
        doppler_margin = doppler_training + doppler_guard
        range_margin = range_training + range_guard
        for doppler_bin in range(doppler_margin, power.shape[0] - doppler_margin):
            for range_bin in range(1, power.shape[1]):
                cells = []
                if range_bin < range_margin:
                    first = range_bin + range_guard + 1
                    last = min(first + range_training, power.shape[1])
                    for row in range(
                        doppler_bin - doppler_margin,
                        doppler_bin + doppler_margin + 1,
                    ):
                        cells.extend(power[row, first:last])
                elif range_bin < power.shape[1] - range_margin:
                    for row in range(
                        doppler_bin - doppler_margin,
                        doppler_bin + doppler_margin + 1,
                    ):
                        for column in range(
                            range_bin - range_margin,
                            range_bin + range_margin + 1,
                        ):
                            in_guard = (
                                abs(row - doppler_bin) <= doppler_guard
                                and abs(column - range_bin) <= range_guard
                            )
                            if not in_guard:
                                cells.append(power[row, column])
                expected_counts[doppler_bin, range_bin] = len(cells)
                expected_sums[doppler_bin, range_bin] = sum(cells)

        np.testing.assert_allclose(sums, expected_sums, atol=1e-12)
        np.testing.assert_array_equal(counts, expected_counts)

    def test_clipped_one_sided_training_requires_minimum_cell_count(self):
        config = RadarConfig(cfar_threshold_db=6.0)
        power = np.ones((16, 5), dtype=float)
        power[8, 1] = 100.0

        sums, counts = cfar_training_stats(power)
        mask, noise_power = ca_cfar_2d(
            power, config, minimum_training_cells=12
        )

        self.assertEqual(counts[8, 1], 11)
        self.assertAlmostEqual(sums[8, 1], 11.0)
        self.assertEqual(counts[8, 3], 0)
        self.assertFalse(mask[8, 1])
        self.assertTrue(np.isnan(noise_power[8, 1]))

    def test_local_peak_compares_against_raw_power_neighbors(self):
        power = np.zeros((3, 3), dtype=float)
        power[1, 1] = 10.0
        power[1, 2] = 12.0
        detections = np.zeros((3, 3), dtype=bool)
        detections[1, 1] = True

        peaks = local_peak_mask(power, detections)

        self.assertFalse(peaks[1, 1])
        self.assertEqual(cluster_detections(power, detections), ())

    def test_equal_adjacent_peaks_cluster_to_lexicographically_first_cell(self):
        power = np.zeros((4, 4), dtype=float)
        detections = np.zeros((4, 4), dtype=bool)
        power[1, 1] = power[1, 2] = 10.0
        detections[1, 1] = detections[1, 2] = True

        first = cluster_detections(power, detections)
        second = cluster_detections(power, detections)

        self.assertEqual(first, ((1, 1),))
        self.assertEqual(second, first)

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

    def test_display_edge_keeps_needed_cfar_training_columns(self):
        config = reduced_config(cfar_threshold_db=6.0, max_display_range_m=22.5)
        shape = (config.doppler_fft_size, config.range_fft_size // 2 + 1)
        power = np.ones(shape, dtype=float)
        power[16, 3] = 100.0
        ranges = np.arange(shape[1], dtype=float) * 7.5
        velocities = (np.arange(shape[0], dtype=float) - 16.0) * 0.25

        targets = detect_targets(
            power,
            ranges,
            velocities,
            config,
            timestamp=4.5,
            sync_score=1.0,
            phase_consistency=1.0,
        )

        self.assertEqual([(target.doppler_bin, target.range_bin) for target in targets], [(16, 3)])

    def test_confidence_increases_with_margin_and_diagnostic_quality(self):
        config = reduced_config(cfar_threshold_db=6.0, max_display_range_m=200.0)
        shape = (config.doppler_fft_size, config.range_fft_size // 2 + 1)
        ranges = np.arange(shape[1], dtype=float) * 7.5
        velocities = (np.arange(shape[0], dtype=float) - 16.0) * 0.25

        def confidence(power_value, sync_score, phase_consistency):
            power = np.ones(shape, dtype=float)
            power[16, 3] = power_value
            return detect_targets(
                power,
                ranges,
                velocities,
                config,
                timestamp=1.0,
                sync_score=sync_score,
                phase_consistency=phase_consistency,
            )[0].confidence

        low_margin = confidence(5.0, 1.0, 1.0)
        high_margin = confidence(100.0, 1.0, 1.0)
        low_quality = confidence(100.0, 0.0, 0.0)

        self.assertGreater(high_margin, low_margin)
        self.assertGreater(high_margin, low_quality)


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

        self.assertEqual(len(frame.targets), 1)
        self.assert_target_matches(frame.targets[0], truth, config)
        self.assertEqual(frame.targets[0].target_id, "T01")
        self.assertEqual(frame.targets[0].timestamp, frame.timestamp)
        self.assertEqual(frame.frame_index, frame.diagnostics.frame_index)

    def test_default_physics_detects_targets_in_first_two_range_bins(self):
        config = RadarConfig()
        for range_m in (7.0, 14.0):
            with self.subTest(range_m=range_m):
                truth = SyntheticTarget(
                    "near", range_m, 0.0, amplitude=0.5, snr_db=30.0
                )

                frame = self.process_targets(config, (truth,), seed=17)

                self.assertEqual(len(frame.targets), 1)
                self.assert_target_matches(frame.targets[0], truth, config)
                self.assertIn(frame.targets[0].range_bin, (1, 2))

    def test_moving_single_target_is_detected(self):
        config = reduced_config()
        truth = SyntheticTarget("truth", 37.5, 1.2, amplitude=0.4, snr_db=24.0)

        frame = self.process_targets(config, (truth,), seed=8)

        self.assertEqual(len(frame.targets), 1)
        self.assert_target_matches(frame.targets[0], truth, config)

    def test_two_separated_targets_are_detected(self):
        config = reduced_config()
        truth = (
            SyntheticTarget("A", 15.0, -1.7, amplitude=0.6, snr_db=28.0),
            SyntheticTarget("B", 45.0, 2.1, amplitude=0.35, snr_db=25.0),
        )

        frame = self.process_targets(config, truth, seed=9)

        self.assertEqual(len(frame.targets), 2)
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

        self.assertEqual(len(frame.targets), 2)
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

        self.assertEqual(len(frame.targets), 1)
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
        total_false_alarms = sum(len(cells) for cells in detected_cells)
        adjacent_persistence = any(
            abs(first_row - second_row) <= 1
            and abs(first_column - second_column) <= 1
            for index, first in enumerate(detected_cells)
            for second in detected_cells[index + 1 :]
            for first_row, first_column in first
            for second_row, second_column in second
        )
        self.assertEqual(total_false_alarms, 0)
        self.assertEqual(persistent, set())
        self.assertFalse(adjacent_persistence)

    def test_near_static_clutter_does_not_hide_or_duplicate_distant_target(self):
        config = reduced_config()
        truth = SyntheticTarget("distant", 45.0, 1.5, amplitude=0.25, snr_db=25.0)
        clutter_range_m = 14.0

        frame = self.process_targets(
            config,
            (truth,),
            seed=12,
            clutter_amplitude=4.0,
            clutter_range_m=clutter_range_m,
        )

        self.assertEqual(len(frame.targets), 2)
        self.assertEqual(
            sum(
                abs(target.range_m - clutter_range_m) <= config.range_resolution_m
                and abs(target.radial_velocity_mps)
                <= config.velocity_resolution_mps
                for target in frame.targets
            ),
            1,
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


class RadarFrameConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig(
            carrier_hz=24e9,
            sample_rate_hz=1e6,
            bandwidth_hz=200e3,
            active_time_s=64e-6,
            idle_time_s=16e-6,
            chirp_count=32,
            range_fft_size=64,
            doppler_fft_size=32,
        )
        self.ranges = np.arange(33, dtype=float)
        self.velocities = np.arange(32, dtype=float)
        self.map_db = np.zeros((32, 33), dtype=float)

    def diagnostics(self, frame_index=3):
        return RadarDiagnostics(
            frame_index=frame_index,
            source="iq",
            sync_ok=True,
            sync_score=1.0,
            phase_consistency=1.0,
            rms=-20.0,
            peak=-10.0,
            clipping=False,
            clip_ratio=0.0,
            noise_floor_db=-60.0,
            processing_time_ms=1.0,
            overruns=0,
        )

    def target(self, timestamp=2.0, range_m=2.0):
        return RadarTarget(
            target_id="T01",
            range_m=range_m,
            radial_velocity_mps=16.0,
            azimuth_deg=None,
            snr_db=20.0,
            power_db=-10.0,
            range_bin=2,
            doppler_bin=16,
            confidence=0.9,
            timestamp=timestamp,
        )

    def frame(self, *, diagnostics=None, targets=()):
        return RadarFrame(
            frame_index=3,
            timestamp=2.0,
            config_snapshot=self.config,
            range_axis_m=self.ranges,
            velocity_axis_mps=self.velocities,
            range_doppler_db=self.map_db,
            targets=targets,
            diagnostics=self.diagnostics() if diagnostics is None else diagnostics,
        )

    def test_rejects_inconsistent_diagnostics_and_target_measurements(self):
        with self.assertRaisesRegex(ValueError, "diagnostics frame_index"):
            self.frame(diagnostics=self.diagnostics(frame_index=4))
        with self.assertRaisesRegex(ValueError, "target timestamp"):
            self.frame(targets=(self.target(timestamp=3.0),))
        with self.assertRaisesRegex(ValueError, "target range"):
            self.frame(targets=(self.target(range_m=9.0),))


if __name__ == "__main__":
    unittest.main()
