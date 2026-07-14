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


class FmcwProcessorTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig()

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

        class CaptureWithoutTruth:
            timestamp = simulated.timestamp
            config = simulated.config
            tx_iq = simulated.tx_iq
            rx_iq = simulated.rx_iq

            @property
            def truth_targets(self):
                raise AssertionError("processor read capture.truth_targets")

        frame = FmcwProcessor(self.config).process(CaptureWithoutTruth())

        self.assertEqual(frame.targets, ())
        self.assertTrue(frame.diagnostics.sync_ok)
        self.assertEqual(frame.diagnostics.source, "iq")
        self.assertTrue(np.isfinite(frame.diagnostics.rms))
        self.assertTrue(np.isfinite(frame.diagnostics.peak))
        self.assertTrue(np.isfinite(frame.diagnostics.noise_floor_db))
        self.assertGreaterEqual(frame.diagnostics.phase_consistency, 0.0)
        self.assertLessEqual(frame.diagnostics.phase_consistency, 1.0)
        self.assertGreaterEqual(frame.diagnostics.processing_time_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
