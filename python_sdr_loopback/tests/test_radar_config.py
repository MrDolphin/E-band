import unittest

import numpy as np

from sdr_loopback.radar.config import RadarConfig, recommended_range_fft_size
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
        self.assertAlmostEqual(config.cpi_duration_s, 9.216e-3)
        self.assertEqual(config.range_fft_size, 4096)
        self.assertAlmostEqual(config.range_resolution_m, 7.49481145, places=5)
        self.assertAlmostEqual(config.max_unambiguous_velocity_mps, 6.85, delta=0.03)
        self.assertAlmostEqual(config.velocity_resolution_mps, 0.214, delta=0.01)

    def test_non_integral_sample_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer sample count"):
            RadarConfig(active_time_s=128.01e-6)

    def test_physical_values_must_be_finite_and_positive(self):
        invalid = (
            {"carrier_hz": float("nan")},
            {"sample_rate_hz": float("inf")},
            {"bandwidth_hz": -1.0},
            {"active_time_s": -1.0},
            {"idle_time_s": -1.0},
            {"chirp_count": 0},
            {"max_display_range_m": 0.0},
            {"cfar_threshold_db": float("nan")},
        )
        for values in invalid:
            with self.subTest(**values):
                with self.assertRaises(ValueError):
                    RadarConfig(**values)

    def test_fft_sizes_must_be_positive_powers_of_two(self):
        invalid_sizes = (
            {"range_fft_size": 4095},
            {"range_fft_size": 5000},
            {"range_fft_size": 0},
            {"doppler_fft_size": 65},
            {"doppler_fft_size": 96},
            {"doppler_fft_size": 0},
        )
        for values in invalid_sizes:
            with self.subTest(**values):
                with self.assertRaisesRegex(ValueError, "positive power of two"):
                    RadarConfig(**values)

    def test_fft_sizes_must_cover_active_samples_and_chirp_count(self):
        with self.assertRaisesRegex(ValueError, "active samples"):
            RadarConfig(range_fft_size=2048)
        with self.assertRaisesRegex(ValueError, "chirp_count"):
            RadarConfig(doppler_fft_size=32)

    def test_recommended_range_fft_scales_to_56_mhz_profile(self):
        self.assertEqual(recommended_range_fft_size(30e6, 128e-6), 4096)
        self.assertEqual(recommended_range_fft_size(61.44e6, 125e-6), 8192)


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
            sync_score=0.98,
            phase_consistency=0.99,
            rms=0.1,
            peak=1.0,
            clipping=False,
            clip_ratio=0.0,
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
            range_axis_m=np.zeros(config.range_fft_size // 2 + 1),
            velocity_axis_mps=np.zeros(config.doppler_fft_size),
            range_doppler_db=np.zeros(
                (config.doppler_fft_size, config.range_fft_size // 2 + 1)
            ),
            targets=(),
            diagnostics=diagnostics,
        )
        self.assertEqual(capture.tx_iq.shape, (config.cpi_samples,))
        self.assertEqual(frame.range_doppler_db.shape, (64, 2049))
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

    def test_capture_accepts_rx_sync_margin_and_validates_start_metadata(self):
        config = RadarConfig()
        start_sample = 23
        capture = RadarCapture(
            timestamp=1.0,
            config=config,
            tx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
            rx_iq=np.zeros(config.cpi_samples + start_sample, dtype=np.complex64),
            chirp_start_sample=start_sample,
        )
        self.assertEqual(capture.chirp_start_sample, start_sample)
        self.assertEqual(capture.rx_iq.shape, (config.cpi_samples + start_sample,))

        with self.assertRaisesRegex(ValueError, "chirp_start_sample"):
            RadarCapture(
                timestamp=1.0,
                config=config,
                tx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
                rx_iq=np.zeros(config.cpi_samples, dtype=np.complex64),
                chirp_start_sample=-1,
            )

        with self.assertRaisesRegex(ValueError, "tx_iq"):
            RadarCapture(
                timestamp=1.0,
                config=config,
                tx_iq=np.zeros(config.cpi_samples + 1, dtype=np.complex64),
                rx_iq=np.zeros(config.cpi_samples + 1, dtype=np.complex64),
            )


if __name__ == "__main__":
    unittest.main()
