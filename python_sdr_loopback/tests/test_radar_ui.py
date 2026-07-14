import unittest

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import RadarDiagnostics, RadarFrame, RadarTarget
from sdr_loopback.radar.ui import (
    RadarDashboard,
    axis_ticks,
    downsample_heatmap,
    format_derived_config,
    format_diagnostics,
    format_target,
    poll_radar,
    radar_xy,
    radar_config_from_values,
    shutdown_runtimes,
)


class RadarUiTests(unittest.TestCase):
    def setUp(self):
        config = RadarConfig(
            sample_rate_hz=1e6,
            bandwidth_hz=1e6,
            active_time_s=64e-6,
            idle_time_s=0.0,
            chirp_count=32,
            range_fft_size=128,
            doppler_fft_size=32,
        )
        ranges = np.linspace(0.0, 50.0, 65)
        velocities = np.linspace(-4.0, 4.0, 32)
        timestamp = 3.0
        target = RadarTarget(
            "T1", ranges[4], velocities[7], None, 18.2, -12.0, 4, 7, 0.91, timestamp
        )
        diagnostics = RadarDiagnostics(
            4, "synthetic", True, 0.97, 0.88, -24.0, -8.0, False, 0.0, -62.0, 4.2, 2
        )
        self.frame = RadarFrame(
            4,
            timestamp,
            config,
            ranges,
            velocities,
            np.zeros((32, 65)),
            (target,),
            diagnostics,
        )

    def test_radar_xy_places_unknown_azimuth_on_boresight(self):
        x, y = radar_xy(25.0, None, 200, 50.0)

        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, -100.0)

    def test_heatmap_downsampling_preserves_visible_grid_and_peaks(self):
        values = np.zeros((64, 130), dtype=float)
        values[-1, -1] = 27.0

        cells = downsample_heatmap(values, rows=32, columns=64)

        self.assertEqual(cells.shape, (32, 64))
        self.assertEqual(cells[-1, -1], 27.0)
        self.assertEqual(
            downsample_heatmap(np.zeros((4, 8)), rows=32, columns=64).shape,
            (32, 64),
        )

    def test_ticks_and_rows_use_radar_units_and_single_rx_language(self):
        target = RadarTarget(
            "T1", 25.0, -1.5, None, 18.2, -12.0, 4, 7, 0.91, 3.0
        )

        self.assertEqual(axis_ticks(-90.0, 90.0, 7), (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0))
        row = format_target(target)
        for expected in ("T1", "25.00 m", "-1.50 m/s", "方位角未测量", "18.2 dB", "91%"):
            with self.subTest(expected=expected):
                self.assertIn(expected, row)

    def test_diagnostics_and_derived_values_are_chinese_and_config_owned(self):
        diagnostics = type(
            "Diagnostics",
            (),
            dict(
                frame_index=8,
                source="synthetic",
                sync_ok=True,
                sync_score=0.97,
                phase_consistency=0.88,
                rms=-24.0,
                peak=-8.0,
                clipping=False,
                clip_ratio=0.0,
                noise_floor_db=-62.0,
                processing_time_ms=4.2,
                overruns=2,
            ),
        )()

        text = format_diagnostics(diagnostics)
        derived = format_derived_config(RadarConfig())

        for expected in ("帧: 8", "源: synthetic", "同步: 正常", "相位一致性: 0.880", "超时: 2"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        for expected in ("3840", "7.49 m", "±6.85 m/s", "0.21 m/s", "9.216 ms"):
            with self.subTest(expected=expected):
                self.assertIn(expected, derived)

    def test_poll_renders_only_changed_frame_and_passes_same_object(self):
        frames = [self.frame, self.frame]

        class Controller:
            def latest_frame(self):
                return frames.pop(0)

        rendered = []
        dashboard = type("Dashboard", (), {"render": lambda _self, frame: rendered.append(frame)})()

        index = poll_radar(Controller(), dashboard, None)
        index = poll_radar(Controller(), dashboard, index)

        self.assertEqual(index, 4)
        self.assertEqual(rendered, [self.frame])
        self.assertIs(rendered[0], self.frame)

    def test_dashboard_gives_every_pane_the_same_complete_frame(self):
        received = []
        pane = lambda: type("Pane", (), {"render": lambda _self, frame: received.append(frame)})()
        dashboard = type(
            "DashboardState",
            (),
            dict(radar=pane(), heatmap=pane(), targets=pane(), diagnostics=pane()),
        )()

        RadarDashboard.render(dashboard, self.frame)

        self.assertEqual(len(received), 4)
        self.assertTrue(all(frame is self.frame for frame in received))

    def test_shutdown_stops_video_then_radar_before_destroy(self):
        calls = []
        controller = type("Controller", (), {"stop": lambda _self: calls.append("radar")})()

        shutdown_runtimes(
            lambda: calls.append("video"),
            controller,
            lambda: calls.append("destroy"),
        )

        self.assertEqual(calls, ["video", "radar", "destroy"])

    def test_display_fields_build_and_validate_radar_config(self):
        values = dict(
            carrier_ghz="76",
            sample_rate_msps="30",
            bandwidth_mhz="20",
            active_us="128",
            idle_us="16",
            chirp_count="64",
            cfar_threshold_db="12",
            max_display_range_m="50",
        )

        self.assertEqual(radar_config_from_values(values).active_samples, 3840)
        with self.assertRaisesRegex(ValueError, "max_display_range_m"):
            radar_config_from_values({**values, "max_display_range_m": "0"})


if __name__ == "__main__":
    unittest.main()
