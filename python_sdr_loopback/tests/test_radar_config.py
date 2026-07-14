import unittest

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import (
    RadarCapture,
    RadarDiagnostics,
    RadarFrame,
    RadarTarget,
    SyntheticTarget,
)


class RadarConfigTests(unittest.TestCase):
    def test_default_dimensions_and_physics(self):
        config = RadarConfig()
        self.assertEqual(config.active_samples, 3840)
        self.assertEqual(config.samples_per_chirp, 4320)
        self.assertEqual(config.cpi_samples, 276480)
        self.assertEqual(config.range_fft_size, 4096)
        self.assertAlmostEqual(config.range_resolution_m, 7.49481145, places=5)
        self.assertAlmostEqual(config.max_unambiguous_velocity_mps, 6.85, delta=0.03)
        self.assertAlmostEqual(config.velocity_resolution_mps, 0.214, delta=0.01)

    def test_non_integral_sample_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer sample count"):
            RadarConfig(active_time_s=128.01e-6)


class RadarModelTests(unittest.TestCase):
    def test_single_rx_target_has_no_angle(self):
        target = RadarTarget(
            target_id="T01",
            range_m=22.5,
            radial_velocity_mps=1.2,
            azimuth_deg=None,
            snr_db=18.0,
            power_db=-12.0,
            range_bin=3,
            doppler_bin=38,
            confidence=0.9,
            timestamp=1.0,
        )
        self.assertIsNone(target.azimuth_deg)

    def test_array_models_validate_shapes_and_ignore_array_equality(self):
        config = RadarConfig()
        diagnostics = RadarDiagnostics(
            frame_index=1,
            source="synthetic",
            sync_ok=True,
            phase_consistency=0.99,
            rms=0.1,
            peak=1.0,
            clipping=False,
            noise_floor_db=-60.0,
            processing_time_ms=2.0,
            overruns=0,
        )
        capture = RadarCapture(
            timestamp=1.0,
            config=config,
            tx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
            rx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
            truth_targets=(SyntheticTarget(target_id="T01", range_m=22.5, radial_velocity_mps=1.2),),
        )
        frame = RadarFrame(
            frame_index=1,
            timestamp=1.0,
            config_snapshot=config,
            range_axis_m=np.zeros(config.range_fft_size),
            velocity_axis_mps=np.zeros(config.doppler_fft_size),
            range_doppler_db=np.zeros((config.doppler_fft_size, config.range_fft_size)),
            targets=(),
            diagnostics=diagnostics,
        )
        self.assertEqual(capture.tx_iq.shape, (config.cpi_samples,))
        self.assertEqual(frame.range_doppler_db.shape, (64, 4096))
        self.assertEqual(capture, capture)
        self.assertEqual(frame, frame)

    def test_array_model_rejects_wrong_shape(self):
        config = RadarConfig()
        with self.assertRaisesRegex(ValueError, "rx_iq"):
            RadarCapture(
                timestamp=1.0,
                config=config,
                tx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
                rx_iq=np.zeros(config.cpi_samples - 1, dtype=np.complex64),
            )


if __name__ == "__main__":
    unittest.main()
