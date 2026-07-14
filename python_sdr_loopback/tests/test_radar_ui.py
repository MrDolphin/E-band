import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from scripts.sdr_video_gui import SdrVideoGui, create_radar_runtime
from sdr_loopback.radar.models import RadarCapture, RadarDiagnostics, RadarFrame, RadarTarget
from sdr_loopback.radar.storage import save_capture
from sdr_loopback.radar.ui import (
    RadarDashboard,
    axis_ticks,
    downsample_heatmap,
    format_derived_config,
    format_diagnostics,
    format_target,
    heatmap_plot_bounds,
    orient_heatmap_for_canvas,
    radar_xy,
    radar_config_from_values,
    validate_runtime_inputs,
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

    def test_heatmap_orientation_puts_positive_velocity_above_negative(self):
        velocity_ordered = np.array([[-4.0, -3.0], [3.0, 4.0]])

        displayed = orient_heatmap_for_canvas(velocity_ordered)

        np.testing.assert_array_equal(displayed[0], [3.0, 4.0])
        np.testing.assert_array_equal(displayed[-1], [-4.0, -3.0])

    def test_heatmap_bounds_fit_a_narrow_canvas_without_losing_cell_grid(self):
        left, top, right, bottom = heatmap_plot_bounds(360, 220)

        self.assertGreaterEqual(right - left, 64)
        self.assertGreaterEqual(bottom - top, 32)
        self.assertLessEqual(right, 360)
        self.assertLessEqual(bottom, 220)

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

    def test_gui_poll_wires_runtime_diagnostics_and_only_renders_changed_frame(self):
        frames = [self.frame, self.frame]
        status = SimpleNamespace(
            overruns=9, error=None, shutdown_error=None, running=True
        )

        class Controller:
            def latest_frame(self):
                return frames.pop(0)

            def status(self):
                return status

        rendered = []
        schedules = []
        gui = SimpleNamespace(
            radar_controller=Controller(),
            _last_radar_frame_index=None,
            radar_source_label="IQ回放",
            render=lambda frame: rendered.append(frame),
            after=lambda delay, callback: schedules.append((delay, callback)),
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)
        SdrVideoGui.poll_radar_controller(gui)

        self.assertEqual(gui._last_radar_frame_index, 4)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0].diagnostics.source, "IQ回放")
        self.assertEqual(rendered[0].diagnostics.overruns, 9)
        self.assertEqual([delay for delay, _ in schedules], [33, 33])

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

    def test_window_close_waits_then_kills_video_before_radar_and_destroy(self):
        calls = []

        class Process:
            def __init__(self):
                self.waits = 0

            def poll(self):
                return None

            def terminate(self):
                calls.append("terminate")

            def wait(self, timeout):
                calls.append("wait")
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("video", timeout)

            def kill(self):
                calls.append("kill")

        gui = SimpleNamespace(
            process=Process(),
            radar_controller=SimpleNamespace(stop=lambda: calls.append("radar")),
            cancel_source_preview_schedule=lambda: calls.append("cancel-preview"),
            stop_source_preview=lambda: calls.append("stop-preview"),
            destroy=lambda: calls.append("destroy"),
        )

        SdrVideoGui.on_close(gui)

        self.assertEqual(
            calls,
            ["cancel-preview", "stop-preview", "terminate", "wait", "kill", "wait", "radar", "destroy"],
        )
        self.assertIsNone(gui.process)
        self.assertIsNone(gui.radar_controller)

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

    def test_gain_and_synthetic_fields_reject_nonfinite_or_negative_range(self):
        invalid = (
            ("nan", "30", "25", "1", "20"),
            ("-30", "inf", "25", "1", "20"),
            ("-30", "30", "nan", "1", "20"),
            ("-30", "30", "-1", "1", "20"),
            ("-30", "30", "25", "inf", "20"),
            ("-30", "30", "25", "1", "nan"),
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_runtime_inputs(*values)

    def test_replay_runtime_uses_capture_config_including_hidden_fft_fields(self):
        replay_config = RadarConfig(
            sample_rate_hz=1e6,
            bandwidth_hz=1e6,
            active_time_s=64e-6,
            idle_time_s=0.0,
            chirp_count=16,
            range_fft_size=256,
            doppler_fft_size=32,
        )
        capture = RadarCapture(
            timestamp=1.0,
            config=replay_config,
            tx_iq=np.zeros(replay_config.cpi_samples, dtype=np.complex64),
            rx_iq=np.zeros(replay_config.cpi_samples, dtype=np.complex64),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nondefault.npz"
            save_capture(path, capture)

            controller, effective_config = create_radar_runtime(
                "IQ回放", self.frame.config_snapshot, path, None
            )

        self.assertEqual(effective_config, replay_config)
        self.assertEqual(controller.processor.config, replay_config)

    def test_gui_replay_start_ignores_invalid_display_and_synthetic_fields(self):
        replay_config = RadarConfig(
            sample_rate_hz=1e6,
            bandwidth_hz=1e6,
            active_time_s=64e-6,
            idle_time_s=0.0,
            chirp_count=16,
            range_fft_size=256,
            doppler_fft_size=32,
        )
        capture = RadarCapture(
            timestamp=1.0,
            config=replay_config,
            tx_iq=np.zeros(replay_config.cpi_samples, dtype=np.complex64),
            rx_iq=np.zeros(replay_config.cpi_samples, dtype=np.complex64),
        )

        class Value:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class Widget:
            def configure(self, **_values):
                pass

        with TemporaryDirectory() as directory:
            path = Path(directory) / "replay.npz"
            save_capture(path, capture)
            invalid = Value("not-a-number")
            gui = SimpleNamespace(
                radar_controller=None,
                radar_source_var=Value("IQ回放"),
                radar_replay_path_var=Value(str(path)),
                radar_carrier_ghz_var=invalid,
                radar_sample_rate_msps_var=invalid,
                radar_bandwidth_mhz_var=invalid,
                radar_active_us_var=invalid,
                radar_idle_us_var=invalid,
                radar_chirp_count_var=invalid,
                radar_cfar_db_var=invalid,
                radar_display_range_var=invalid,
                radar_tx_channel_var=invalid,
                radar_rx_channel_var=invalid,
                radar_tx_gain_var=invalid,
                radar_rx_gain_var=invalid,
                radar_target_range_var=invalid,
                radar_target_velocity_var=invalid,
                radar_target_snr_var=invalid,
                radar_status_var=Value(""),
                radar_derived_var=Value(""),
                radar_start_button=Widget(),
                radar_stop_button=Widget(),
                radar_tab=object(),
                notebook=SimpleNamespace(select=lambda _tab: None),
            )
            gui.build_radar_config = lambda: SdrVideoGui.build_radar_config(gui)

            with patch("scripts.sdr_video_gui.RadarController.start"), patch(
                "scripts.sdr_video_gui.messagebox.showerror"
            ):
                SdrVideoGui.start_radar(gui)

        self.assertEqual(gui.radar_controller.processor.config, replay_config)
        self.assertEqual(gui.radar_source_label, "IQ回放")
        self.assertIn("回放配置", gui.radar_derived_var.get())

    def test_gui_replay_start_reports_a_stable_invalid_path_error(self):
        value = lambda text: SimpleNamespace(get=lambda: text)
        gui = SimpleNamespace(
            radar_controller=None,
            radar_source_var=value("IQ回放"),
            radar_replay_path_var=value("missing-capture.npz"),
        )

        with patch("scripts.sdr_video_gui.messagebox.showerror") as showerror:
            SdrVideoGui.start_radar(gui)

        showerror.assert_called_once_with(
            "雷达参数错误", "请选择有效的IQ回放文件"
        )
        self.assertIsNone(gui.radar_controller)


if __name__ == "__main__":
    unittest.main()
