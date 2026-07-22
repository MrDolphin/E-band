import unittest
from types import SimpleNamespace

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import RadarCapture, SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.simulator import simulate_capture
from sdr_loopback.radar.synchronizer import (
    ChirpSynchronizer,
    ChirpSyncError,
)
from sdr_loopback.radar.waveform import generate_cpi


class ChirpSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig()
        self.tx_iq = generate_cpi(self.config)

    def capture_with_rx(self, rx_iq, tx_iq=None):
        return SimpleNamespace(
            config=self.config,
            tx_iq=self.tx_iq if tx_iq is None else np.asarray(tx_iq),
            rx_iq=np.asarray(rx_iq),
        )

    def test_known_mode_returns_all_aligned_chirps(self):
        result = ChirpSynchronizer(self.config, mode="known").synchronize(
            self.capture_with_rx(self.tx_iq)
        )

        self.assertEqual(result.start_sample, 0)
        self.assertEqual(result.chirp_starts.shape, (self.config.chirp_count,))
        np.testing.assert_array_equal(
            result.chirp_starts,
            np.arange(self.config.chirp_count) * self.config.samples_per_chirp,
        )

    def test_correlation_mode_recovers_leading_offset_within_one_sample(self):
        expected_offset = 37
        offset_rx = np.concatenate(
            (np.zeros(expected_offset, dtype=np.complex64), self.tx_iq)
        )

        result = ChirpSynchronizer(self.config, mode="correlation").synchronize(
            self.capture_with_rx(offset_rx)
        )

        self.assertLessEqual(abs(result.start_sample - expected_offset), 1)
        self.assertEqual(len(result.chirp_starts), self.config.chirp_count)
        self.assertGreater(result.correlation, 0.99)
        self.assertLess(result.idle_to_active_db, -40.0)

    def test_correlation_mode_accepts_repeatable_low_snr_hardware_chirps(self):
        offset = 37
        rng = np.random.default_rng(20260715)
        rx = (
            rng.normal(0.0, 0.5, self.tx_iq.size + offset)
            + 1j * rng.normal(0.0, 0.5, self.tx_iq.size + offset)
        ).astype(np.complex64)
        rx[offset:] += 0.08 * self.tx_iq

        result = ChirpSynchronizer(self.config, mode="correlation").synchronize(
            self.capture_with_rx(rx)
        )

        self.assertEqual(result.start_sample, offset)
        self.assertGreater(result.correlation, 0.05)
        self.assertGreater(result.idle_to_active_db, -1.0)

    def test_correlation_mode_rejects_equal_power_noise(self):
        rng = np.random.default_rng(20260715)
        noise = (
            rng.normal(0.0, 0.5, self.tx_iq.size + 37)
            + 1j * rng.normal(0.0, 0.5, self.tx_iq.size + 37)
        ).astype(np.complex64)

        with self.assertRaisesRegex(ChirpSyncError, "correlation|coherence") as caught:
            ChirpSynchronizer(self.config, mode="correlation").synchronize(
                self.capture_with_rx(noise)
            )

        diagnostics = caught.exception.diagnostics
        self.assertEqual(diagnostics.failed_metric, "min_correlation")
        self.assertLess(
            diagnostics.min_correlation,
            diagnostics.min_correlation_threshold,
        )
        self.assertIn("min_correlation=", str(caught.exception))

    def test_correlation_mode_rejects_incoherent_low_snr_chirps(self):
        rng = np.random.default_rng(20260715)
        rx = (
            rng.normal(0.0, 0.5, self.tx_iq.size)
            + 1j * rng.normal(0.0, 0.5, self.tx_iq.size)
        ).astype(np.complex64)
        rx += 0.08 * self.tx_iq
        chirps = rx.reshape(self.config.chirp_count, self.config.samples_per_chirp)
        chirps *= np.exp(
            1j * rng.uniform(-np.pi, np.pi, (self.config.chirp_count, 1))
        )

        with self.assertRaisesRegex(ChirpSyncError, "periodic coherence"):
            ChirpSynchronizer(self.config, mode="correlation").synchronize(
                self.capture_with_rx(chirps.ravel())
            )

    def test_correlation_mode_declares_minimum_supported_dimensions(self):
        unsupported = (
            RadarConfig(chirp_count=1, doppler_fft_size=1),
            RadarConfig(
                sample_rate_hz=1.0,
                bandwidth_hz=1.0,
                active_time_s=1.0,
                idle_time_s=0.0,
                chirp_count=2,
                range_fft_size=1,
                doppler_fft_size=2,
            ),
        )
        for config in unsupported:
            with self.subTest(config=config):
                with self.assertRaisesRegex(ValueError, "correlation mode requires"):
                    ChirpSynchronizer(config, mode="correlation")

    def test_correlation_mode_is_stable_across_seeds_and_chirp_counts(self):
        accepted_low_snr = 0
        for seed in range(20):
            rng = np.random.default_rng(seed)
            noise = (
                rng.normal(0.0, 0.5, self.tx_iq.size + 37)
                + 1j * rng.normal(0.0, 0.5, self.tx_iq.size + 37)
            ).astype(np.complex64)
            for has_signal in (False, True):
                rx = noise.copy()
                if has_signal:
                    rx[37:] += 0.08 * self.tx_iq
                capture = self.capture_with_rx(rx)
                try:
                    result = ChirpSynchronizer(
                        self.config, mode="correlation"
                    ).synchronize(capture)
                except ChirpSyncError:
                    if not has_signal:
                        continue
                else:
                    self.assertTrue(has_signal)
                    self.assertGreaterEqual(result.periodic_coherence, 0.0)
                    accepted_low_snr += 1
        self.assertGreaterEqual(accepted_low_snr, 19)

        for chirp_count in (2, 8, 64):
            with self.subTest(chirp_count=chirp_count):
                config = RadarConfig(
                    chirp_count=chirp_count,
                    doppler_fft_size=max(2, 1 << (chirp_count - 1).bit_length()),
                )
                tx = generate_cpi(config)
                capture = RadarCapture(0.0, config, tx, tx, chirp_start_sample=0)
                result = ChirpSynchronizer(config, mode="correlation").synchronize(
                    capture
                )
                self.assertGreater(result.periodic_coherence, 0.99)

    def test_incomplete_capture_raises_sync_error(self):
        truncated = self.tx_iq[:-1]

        with self.assertRaisesRegex(ChirpSyncError, "complete chirps"):
            ChirpSynchronizer(self.config, mode="known").synchronize(
                self.capture_with_rx(truncated)
            )

    def test_non_finite_capture_is_rejected(self):
        invalid = self.tx_iq.copy()
        invalid[self.config.active_samples] = np.inf + 1j * np.nan

        with self.assertRaisesRegex(ChirpSyncError, "finite"):
            ChirpSynchronizer(self.config, mode="correlation").synchronize(
                self.capture_with_rx(invalid)
            )

    def test_extreme_finite_capture_rejects_overflowed_sync_metrics(self):
        scale = np.finfo(np.float64).max / 8.0
        extreme_rx = self.tx_iq.astype(np.complex128) * scale
        self.assertTrue(np.all(np.isfinite(extreme_rx)))

        with self.assertRaisesRegex(ChirpSyncError, "finite"):
            ChirpSynchronizer(self.config, mode="correlation").synchronize(
                self.capture_with_rx(extreme_rx)
            )


class FmcwProcessorTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig()

    def test_online_empty_room_calibration_is_applied_after_requested_cpis(self):
        capture = simulate_capture(
            self.config,
            (
                SyntheticTarget(
                    target_id="static-clutter",
                    range_m=7.5,
                    radial_velocity_mps=0.0,
                    amplitude=0.8,
                    snr_db=60.0,
                ),
            ),
            seed=20260716,
        )
        processor = FmcwProcessor(self.config)

        processor.begin_background_calibration(cpi_count=3)
        for _ in range(3):
            processor.process(capture)

        status = processor.background_calibration_status()
        self.assertFalse(status.active)
        self.assertTrue(status.ready)
        self.assertEqual(status.collected_cpis, 3)
        self.assertEqual(status.required_cpis, 3)

        calibrated = processor.process(capture)

        self.assertEqual(calibrated.targets, ())

    def test_online_background_calibration_rejects_invalid_count(self):
        processor = FmcwProcessor(self.config)

        for count in (0, -1, 1.5, True):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    processor.begin_background_calibration(cpi_count=count)

    def test_recalibration_keeps_previous_background_until_atomic_replacement(self):
        capture = simulate_capture(
            self.config,
            (
                SyntheticTarget(
                    target_id="static-clutter",
                    range_m=7.5,
                    radial_velocity_mps=0.0,
                    amplitude=0.8,
                    snr_db=60.0,
                ),
            ),
            seed=20260716,
        )
        processor = FmcwProcessor(self.config)
        processor.begin_background_calibration(cpi_count=2)
        processor.process(capture)
        processor.process(capture)
        self.assertTrue(processor.background_calibration_status().ready)

        processor.begin_background_calibration(cpi_count=2)

        status = processor.background_calibration_status()
        self.assertTrue(status.active)
        self.assertTrue(status.ready)
        self.assertEqual(processor.process(capture).targets, ())

        self.assertTrue(
            processor.cancel_background_calibration("synchronization timeout")
        )

        cancelled = processor.background_calibration_status()
        self.assertFalse(cancelled.active)
        self.assertTrue(cancelled.ready)
        self.assertIn("timeout", cancelled.error)
        self.assertEqual(processor.process(capture).targets, ())

        self.assertFalse(processor.cancel_background_calibration("stale timeout"))
        self.assertIn(
            "timeout", processor.background_calibration_status().error
        )

    def test_online_calibration_phase_aligns_coherent_global_cpi_rotations(self):
        base = simulate_capture(
            self.config,
            (
                SyntheticTarget(
                    target_id="static-clutter",
                    range_m=7.5,
                    radial_velocity_mps=0.0,
                    amplitude=0.8,
                    snr_db=80.0,
                ),
            ),
            seed=11,
        )
        processor = FmcwProcessor(self.config)
        processor.begin_background_calibration(cpi_count=4)
        for phase in (0.0, 0.7, -1.2, 2.1):
            processor.process(
                RadarCapture(
                    base.timestamp,
                    base.config,
                    base.tx_iq,
                    base.rx_iq * np.exp(1j * phase),
                    chirp_start_sample=base.chirp_start_sample,
                )
            )

        calibrated = processor.process(
            RadarCapture(
                base.timestamp,
                base.config,
                base.tx_iq,
                base.rx_iq * np.exp(-0.4j),
                chirp_start_sample=base.chirp_start_sample,
            )
        )

        self.assertTrue(processor.background_calibration_status().ready)
        self.assertEqual(calibrated.targets, ())

    def test_online_calibration_rejects_incoherent_candidates_without_stopping(self):
        processor = FmcwProcessor(self.config)
        processor.begin_background_calibration(cpi_count=3)
        tx = generate_cpi(self.config)
        rng = np.random.default_rng(33)
        for index in range(3):
            rx = (
                rng.normal(size=self.config.cpi_samples)
                + 1j * rng.normal(size=self.config.cpi_samples)
            ).astype(np.complex64)
            processor.process(
                RadarCapture(index, self.config, tx, rx, chirp_start_sample=0)
            )

        status = processor.background_calibration_status()
        self.assertFalse(status.active)
        self.assertFalse(status.ready)
        self.assertIn("coherence", status.error)

    def test_single_target_peak_localizes_range_and_positive_velocity(self):
        truth = SyntheticTarget(
            target_id="T01",
            range_m=22.5,
            radial_velocity_mps=1.2,
            amplitude=0.5,
            snr_db=40.0,
        )
        capture = simulate_capture(self.config, (truth,), seed=7)

        frame = FmcwProcessor(self.config).process(capture)
        peak = np.unravel_index(
            np.argmax(frame.range_doppler_db), frame.range_doppler_db.shape
        )

        self.assertLessEqual(
            abs(frame.range_axis_m[peak[1]] - truth.range_m),
            self.config.range_resolution_m,
        )
        self.assertLessEqual(
            abs(frame.velocity_axis_mps[peak[0]] - truth.radial_velocity_mps),
            self.config.velocity_resolution_mps,
        )
        self.assertGreater(frame.velocity_axis_mps[peak[0]], 0.0)
        self.assertEqual(frame.range_doppler_db.shape, (64, 2049))

    def test_known_mode_uses_nonzero_capture_start_metadata(self):
        truth = SyntheticTarget(
            target_id="T02",
            range_m=22.5,
            radial_velocity_mps=1.2,
            amplitude=0.5,
            snr_db=40.0,
        )
        simulated = simulate_capture(self.config, (truth,), seed=13)
        prefix_samples = 41
        capture = RadarCapture(
            timestamp=simulated.timestamp,
            config=self.config,
            tx_iq=simulated.tx_iq,
            rx_iq=np.concatenate(
                (np.zeros(prefix_samples, dtype=np.complex64), simulated.rx_iq)
            ),
            truth_targets=(truth,),
            chirp_start_sample=prefix_samples,
        )

        frame = FmcwProcessor(self.config, sync_mode="known").process(capture)
        peak = np.unravel_index(
            np.argmax(frame.range_doppler_db), frame.range_doppler_db.shape
        )

        self.assertLessEqual(
            abs(frame.range_axis_m[peak[1]] - truth.range_m),
            self.config.range_resolution_m,
        )
        self.assertLessEqual(
            abs(frame.velocity_axis_mps[peak[0]] - truth.radial_velocity_mps),
            self.config.velocity_resolution_mps,
        )

    def test_non_finite_receive_iq_is_rejected_before_processing(self):
        capture = simulate_capture(self.config)
        invalid_rx = capture.rx_iq.copy()
        invalid_rx[123] = np.nan + 1j * np.inf
        invalid_capture = RadarCapture(
            timestamp=capture.timestamp,
            config=capture.config,
            tx_iq=capture.tx_iq,
            rx_iq=invalid_rx,
        )

        with self.assertRaisesRegex(ChirpSyncError, "finite"):
            FmcwProcessor(self.config).process(invalid_capture)

    def test_processor_does_not_read_truth_targets_and_reports_measurements(self):
        simulated = simulate_capture(
            self.config,
            (
                SyntheticTarget(
                    target_id="T01",
                    range_m=30.0,
                    radial_velocity_mps=0.8,
                    amplitude=0.25,
                    snr_db=30.0,
                ),
            ),
            seed=11,
        )
        measured_rx = simulated.rx_iq.copy()
        measured_rx[:17] = 1.25 + 0.0j
        expected_clip_ratio = float(np.mean(np.abs(measured_rx) >= 1.0))

        class CaptureWithoutTruth:
            timestamp = simulated.timestamp
            config = simulated.config
            tx_iq = simulated.tx_iq
            rx_iq = measured_rx

            @property
            def truth_targets(self):
                raise AssertionError("processor read capture.truth_targets")

        capture = CaptureWithoutTruth()
        expected_sync_score = ChirpSynchronizer(
            self.config, mode="known"
        ).synchronize(capture).correlation
        frame = FmcwProcessor(self.config).process(capture)

        self.assertGreaterEqual(len(frame.targets), 1)
        self.assertLessEqual(
            abs(frame.targets[0].range_m - 30.0), self.config.range_resolution_m
        )
        self.assertLessEqual(
            abs(frame.targets[0].radial_velocity_mps - 0.8),
            self.config.velocity_resolution_mps,
        )
        self.assertTrue(frame.diagnostics.sync_ok)
        self.assertEqual(frame.diagnostics.source, "iq")
        self.assertAlmostEqual(frame.diagnostics.sync_score, expected_sync_score)
        self.assertAlmostEqual(frame.diagnostics.clip_ratio, expected_clip_ratio)
        self.assertTrue(frame.diagnostics.clipping)
        self.assertTrue(np.isfinite(frame.diagnostics.rms))
        self.assertTrue(np.isfinite(frame.diagnostics.peak))
        self.assertTrue(np.isfinite(frame.diagnostics.noise_floor_db))
        self.assertGreaterEqual(frame.diagnostics.phase_consistency, 0.0)
        self.assertLessEqual(frame.diagnostics.phase_consistency, 1.0)
        self.assertGreaterEqual(frame.diagnostics.processing_time_ms, 0.0)

    def test_low_phase_consistency_retains_range_but_marks_velocity_untrusted(self):
        truth = SyntheticTarget("T01", 22.5, 1.2, amplitude=0.5, snr_db=40.0)
        capture = simulate_capture(self.config, (truth,), seed=17)
        rng = np.random.default_rng(3)
        randomized = capture.rx_iq.reshape(self.config.chirp_count, -1).copy()
        randomized *= np.exp(1j * rng.uniform(-np.pi, np.pi, (self.config.chirp_count, 1)))
        capture = RadarCapture(
            timestamp=capture.timestamp,
            config=capture.config,
            tx_iq=capture.tx_iq,
            rx_iq=randomized.ravel(),
        )

        frame = FmcwProcessor(self.config).process(capture)

        self.assertGreaterEqual(len(frame.targets), 1)
        self.assertLessEqual(
            abs(frame.targets[0].range_m - truth.range_m),
            self.config.range_resolution_m,
        )
        self.assertFalse(frame.targets[0].velocity_trusted)
        self.assertEqual(frame.targets[0].velocity_confidence, 0.0)

    def test_adjacent_chirp_correlation_distinguishes_coherent_and_orthogonal_rows(self):
        coherent = np.ones((3, 4), dtype=np.complex128)
        orthogonal = np.asarray(
            ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            dtype=np.complex128,
        )

        self.assertAlmostEqual(FmcwProcessor._adjacent_chirp_correlation(coherent), 1.0)
        self.assertAlmostEqual(FmcwProcessor._adjacent_chirp_correlation(orthogonal), 0.0)

    def test_velocity_trust_threshold_must_be_finite_probability(self):
        for threshold in (-0.1, 1.1, np.nan, np.inf):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, "velocity_trust_threshold"):
                    FmcwProcessor(self.config, velocity_trust_threshold=threshold)


if __name__ == "__main__":
    unittest.main()
