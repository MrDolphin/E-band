import unittest
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from scripts.sdr_video_gui import (
    SdrVideoGui,
    create_radar_runtime,
    format_radar_runtime_diagnostics,
)
from sdr_loopback.radar.models import RadarCapture, RadarDiagnostics, RadarFrame, RadarTarget
from sdr_loopback.radar.sources import E310RadioConfig
from sdr_loopback.radar.storage import save_capture
from sdr_loopback.radar.synchronizer import ChirpSyncError
from sdr_loopback.radar.ui import (
    RadarDashboard,
    axis_cell_edges,
    axis_ticks,
    downsample_axis_edges,
    downsample_heatmap,
    dashboard_column_options,
    equal_dashboard_widths,
    format_derived_config,
    format_diagnostics,
    format_plot_target,
    format_target,
    heatmap_plot_bounds,
    heatmap_color_limits,
    orient_heatmap_for_canvas,
    radar_xy,
    radar_config_from_values,
    radar_bandwidth_profile,
    range_doppler_y_axis_layout,
    semicircle_label_layout,
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

    def test_semicircle_outer_range_and_zero_tick_labels_have_clear_gap(self):
        rings, zero_tick = semicircle_label_layout(210.0, 250.0, 190.0, 50.0)

        self.assertEqual(tuple(rings), (10, 20, 30, 40, 50))
        self.assertEqual(rings[50][2], "n")
        self.assertGreater(rings[50][1], 250.0)
        self.assertEqual(zero_tick[2], "center")
        self.assertGreaterEqual(rings[50][0] - zero_tick[0], 30.0)

    def test_plot_target_label_contains_range_and_only_trusted_velocity(self):
        trusted = RadarTarget(
            "T01", 14.05, 1.25, None, 20.0, -10.0, 2, 3, 0.8, 1.0,
            velocity_confidence=0.9, velocity_trusted=True,
        )
        untrusted = RadarTarget(
            "T02", 7.03, 0.0, None, 18.0, -12.0, 1, 3, 0.7, 1.0,
            velocity_confidence=0.0, velocity_trusted=False,
        )

        self.assertEqual(format_plot_target(trusted), "T01  14.05 m / +1.25 m/s")
        self.assertEqual(format_plot_target(untrusted), "T02  7.03 m / 速度不可信")

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

        boundary_peak = np.zeros((1, 130), dtype=float)
        boundary_peak[0, 2] = 27.0
        pooled = downsample_heatmap(boundary_peak, rows=1, columns=64)
        self.assertEqual(np.count_nonzero(pooled), 1)

    def test_axis_edges_downsampling_uses_original_nonoverlapping_bin_edges(self):
        centers = np.arange(130, dtype=float)

        edges = downsample_axis_edges(
            centers,
            count=64,
            minimum=0.0,
            maximum=130.0,
        )

        self.assertEqual(edges.shape, (65,))
        self.assertTrue(np.all(np.diff(edges) > 0.0))
        np.testing.assert_allclose(edges[:3], [0.0, 2.5, 5.5])
        self.assertEqual(edges[-1], 130.0)

    def test_heatmap_orientation_puts_positive_velocity_above_negative(self):
        velocity_ordered = np.array([[-4.0, -3.0], [3.0, 4.0]])

        displayed = orient_heatmap_for_canvas(velocity_ordered)

        np.testing.assert_array_equal(displayed[0], [3.0, 4.0])
        np.testing.assert_array_equal(displayed[-1], [-4.0, -3.0])

    def test_range_cell_edges_use_physical_fft_midpoints(self):
        edges = axis_cell_edges(
            np.array([0.0, 7.03, 14.05, 21.08]),
            minimum=0.0,
            maximum=25.0,
        )

        np.testing.assert_allclose(
            edges,
            [0.0, 3.515, 10.54, 17.565, 25.0],
            atol=1e-6,
        )

    def test_heatmap_color_scale_is_anchored_to_noise_floor(self):
        floor, ceiling = heatmap_color_limits(
            np.array([[-120.0, -80.0, -20.0]]),
            noise_floor_db=-92.0,
            dynamic_range_db=30.0,
        )

        self.assertEqual(floor, -92.0)
        self.assertEqual(ceiling, -62.0)

    def test_heatmap_bounds_fit_a_narrow_canvas_without_losing_cell_grid(self):
        left, top, right, bottom = heatmap_plot_bounds(360, 220)

        self.assertGreaterEqual(right - left, 64)
        self.assertGreaterEqual(bottom - top, 32)
        self.assertLessEqual(right, 360)
        self.assertLessEqual(bottom, 220)

    def test_range_doppler_vertical_title_uses_separate_rotated_margin(self):
        title, middle_tick = range_doppler_y_axis_layout(72, 20, 186)

        self.assertEqual(title[2:], ("center", 90))
        self.assertEqual(middle_tick[2], "e")
        self.assertGreaterEqual(middle_tick[0] - title[0], 40)

    def test_dashboard_plot_columns_are_uniform_and_split_width_equally(self):
        options = dashboard_column_options()

        self.assertEqual(options, {"weight": 1, "uniform": "radar-plots"})
        for total_width in (900, 901):
            with self.subTest(total_width=total_width):
                left, right = equal_dashboard_widths(total_width)
                self.assertLessEqual(abs(left - right), 1)
                self.assertEqual(left + right, total_width)

    def test_ticks_and_rows_use_radar_units_and_single_rx_language(self):
        target = RadarTarget(
            "T1", 25.0, -1.5, None, 18.2, -12.0, 4, 7, 0.91, 3.0
        )

        self.assertEqual(axis_ticks(-90.0, 90.0, 7), (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0))
        row = format_target(target)
        for expected in ("T1", "25.00 m", "-1.50 m/s", "方位角未测量", "18.2 dB", "91%"):
            with self.subTest(expected=expected):
                self.assertIn(expected, row)

        untrusted = RadarTarget(
            "T2", 25.0, -1.5, None, 18.2, -12.0, 4, 7, 0.91, 3.0,
            velocity_confidence=0.0, velocity_trusted=False,
        )
        self.assertIn("速度不可信", format_target(untrusted))
        self.assertNotIn("-1.50 m/s", format_target(untrusted))

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
            radar_controller=SimpleNamespace(
                stop=lambda: calls.append("radar"),
                status=lambda: SimpleNamespace(
                    running=False, cleanup_complete=True
                ),
            ),
            cancel_source_preview_schedule=lambda: calls.append("cancel-preview"),
            stop_source_preview=lambda: calls.append("stop-preview"),
            destroy=lambda: calls.append("destroy"),
        )

        SdrVideoGui.on_close(gui)

        self.assertEqual(
            calls,
            ["cancel-preview", "stop-preview", "terminate", "wait", "kill", "wait", "destroy"],
        )
        self.assertIsNone(gui.process)
        self.assertIsNone(gui.radar_controller)

    def test_window_close_waits_for_radar_cleanup_before_destroying(self):
        calls = []
        controller = SimpleNamespace(
            stop=lambda: calls.append("stop-radar"),
            status=lambda: SimpleNamespace(running=True, cleanup_complete=False),
        )
        gui = SimpleNamespace(
            process=None,
            radar_controller=controller,
            cancel_source_preview_schedule=lambda: calls.append("cancel-preview"),
            stop_source_preview=lambda: calls.append("stop-preview"),
            radar_status_var=SimpleNamespace(
                set=lambda value: calls.append(("status", value))
            ),
            after=lambda delay, callback: calls.append(("after", delay, callback)),
            destroy=lambda: calls.append("destroy"),
        )

        SdrVideoGui.on_close(gui)

        self.assertIs(gui.radar_controller, controller)
        self.assertNotIn("destroy", calls)
        self.assertTrue(any(call[:2] == ("after", 100) for call in calls if isinstance(call, tuple)))

    def test_window_close_does_not_block_tk_thread_on_radar_stop(self):
        controller = SimpleNamespace(
            stop=lambda: time.sleep(0.2),
            status=lambda: SimpleNamespace(
                running=True, cleanup_complete=False
            ),
        )
        gui = SimpleNamespace(
            process=None,
            radar_controller=controller,
            cancel_source_preview_schedule=lambda: None,
            stop_source_preview=lambda: None,
            radar_status_var=SimpleNamespace(set=lambda _value: None),
            after=lambda _delay, _callback: None,
            destroy=lambda: None,
        )

        started = time.monotonic()
        SdrVideoGui.on_close(gui)

        self.assertLess(time.monotonic() - started, 0.1)

    def test_stop_radar_keeps_controller_and_start_disabled_while_cleanup_is_pending(self):
        class Value:
            def __init__(self):
                self.value = ""

            def set(self, value):
                self.value = value

        class Widget:
            def __init__(self):
                self.state = None

            def configure(self, *, state):
                self.state = state

        controller = SimpleNamespace(
            stop=lambda: None,
            status=lambda: SimpleNamespace(
                running=True,
                cleanup_complete=False,
                error=None,
                shutdown_error="radar worker did not stop before timeout",
            ),
        )
        gui = SimpleNamespace(
            radar_controller=controller,
            radar_status_var=Value(),
            radar_start_button=Widget(),
            radar_stop_button=Widget(),
        )

        SdrVideoGui.stop_radar(gui)

        self.assertIs(gui.radar_controller, controller)
        self.assertEqual(gui.radar_start_button.state, "disabled")
        self.assertEqual(gui.radar_stop_button.state, "disabled")
        self.assertIn("停止中", gui.radar_status_var.value)

    def test_poll_releases_controller_after_timed_out_cleanup_finishes(self):
        status = SimpleNamespace(
            running=False,
            cleanup_complete=True,
            error=None,
            shutdown_error="radar worker did not stop before timeout",
            overruns=0,
        )
        controller = SimpleNamespace(
            status=lambda: status,
            latest_frame=lambda: None,
        )
        start_button = SimpleNamespace(state=None)
        stop_button = SimpleNamespace(state=None)
        start_button.configure = lambda *, state: setattr(start_button, "state", state)
        stop_button.configure = lambda *, state: setattr(stop_button, "state", state)
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        gui = SimpleNamespace(
            radar_controller=controller,
            _last_radar_frame_index=None,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_start_button=start_button,
            radar_stop_button=stop_button,
            after=lambda _delay, _callback: None,
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)

        self.assertIsNone(gui.radar_controller)
        self.assertEqual(start_button.state, "normal")
        self.assertEqual(stop_button.state, "disabled")
        self.assertIn("已停止", radar_status.value)

    def test_poll_keeps_start_disabled_when_worker_stopped_but_cleanup_failed(self):
        status = SimpleNamespace(
            running=False,
            cleanup_complete=False,
            error="E310 cleanup failed",
            shutdown_error=None,
            overruns=0,
        )
        controller = SimpleNamespace(status=lambda: status, latest_frame=lambda: None)
        start_button = SimpleNamespace(state=None)
        stop_button = SimpleNamespace(state=None)
        start_button.configure = lambda *, state: setattr(start_button, "state", state)
        stop_button.configure = lambda *, state: setattr(stop_button, "state", state)
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        gui = SimpleNamespace(
            radar_controller=controller,
            _last_radar_frame_index=None,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_start_button=start_button,
            radar_stop_button=stop_button,
            after=lambda _delay, _callback: None,
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)

        self.assertIs(gui.radar_controller, controller)
        self.assertEqual(start_button.state, "disabled")
        self.assertEqual(stop_button.state, "normal")
        self.assertIn("清理失败", radar_status.value)

    def test_empty_room_button_starts_in_session_e310_calibration(self):
        processor = SimpleNamespace(cpi_count=None)
        processor.begin_background_calibration = lambda *, cpi_count: setattr(
            processor, "cpi_count", cpi_count
        )
        controller = SimpleNamespace(
            processor=processor,
            status=lambda: SimpleNamespace(running=True),
        )
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        button = SimpleNamespace(state=None)
        button.configure = lambda *, state: setattr(button, "state", state)
        gui = SimpleNamespace(
            radar_controller=controller,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_calibrate_button=button,
        )

        SdrVideoGui.calibrate_radar_background(gui)

        self.assertEqual(processor.cpi_count, 16)
        self.assertEqual(button.state, "disabled")
        self.assertIn("0/16", radar_status.value)

    def test_candidate_photo_is_copied_into_hardware_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "overhead.jpg"
            photo.write_bytes(b"photo")
            gui = SimpleNamespace(
                radar_candidate_photo_var=SimpleNamespace(get=lambda: str(photo))
            )
            frame = SimpleNamespace(frame_index=7)
            with patch("scripts.sdr_video_gui.PROJECT_DIR", root):
                relative = SdrVideoGui._copy_radar_candidate_photo(gui, frame)

            copied = root / "artifacts" / "hardware" / relative
            self.assertEqual(relative, "position_candidates/photos/candidate_000007.jpg")
            self.assertEqual(copied.read_bytes(), b"photo")

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

    def test_display_fields_scale_range_fft_for_56_mhz_e310_profile(self):
        config = radar_config_from_values(
            dict(
                carrier_ghz="76",
                sample_rate_msps="61.44",
                bandwidth_mhz="56",
                active_us="125",
                idle_us="15.625",
                chirp_count="64",
                cfar_threshold_db="12",
                max_display_range_m="50",
            )
        )

        self.assertEqual(config.active_samples, 7680)
        self.assertEqual(config.range_fft_size, 8192)
        self.assertLess(config.range_resolution_m, 2.7)

    def test_bandwidth_profiles_keep_stable_and_trial_settings_separate(self):
        stable = radar_config_from_values(radar_bandwidth_profile("稳定 20 MHz"))
        trial = radar_config_from_values(radar_bandwidth_profile("极限 56 MHz"))

        self.assertEqual(stable.bandwidth_hz, 20e6)
        self.assertEqual(trial.bandwidth_hz, 56e6)
        self.assertEqual(trial.sample_rate_hz, 61.44e6)
        self.assertGreater(trial.range_fft_size, stable.range_fft_size)

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

    def test_e310_runtime_uses_hardware_source_and_correlation_sync(self):
        config = self.frame.config_snapshot
        radio_config = E310RadioConfig(
            tx_gain_db=-20.0,
            rx_gain_db=20.0,
            tx_amplitude=0.4,
        )

        with patch("scripts.sdr_video_gui.E310CpiSource") as source_type:
            controller, effective_config = create_radar_runtime(
                "E310", config, None, None, radio_config
            )

        source_type.assert_called_once_with(config, radio_config)
        self.assertIs(controller.source, source_type.return_value)
        self.assertEqual(controller.processor._synchronizer.mode, "correlation")
        self.assertEqual(
            controller._recoverable_processing_errors,
            (ChirpSyncError,),
        )
        self.assertEqual(controller._recover_after_processing_errors, 16)
        self.assertEqual(controller._max_source_recoveries, 5)
        self.assertEqual(effective_config, config)

    def test_poll_reports_waiting_for_sync_while_controller_is_running(self):
        status = SimpleNamespace(
            running=True,
            cleanup_complete=False,
            error="chirp correlation is below the sync threshold",
            shutdown_error=None,
            overruns=0,
        )
        controller = SimpleNamespace(status=lambda: status, latest_frame=lambda: None)
        start_button = SimpleNamespace(state=None)
        stop_button = SimpleNamespace(state=None)
        calibrate_button = SimpleNamespace(state=None)
        start_button.configure = lambda *, state: setattr(start_button, "state", state)
        stop_button.configure = lambda *, state: setattr(stop_button, "state", state)
        calibrate_button.configure = lambda *, state: setattr(
            calibrate_button, "state", state
        )
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        gui = SimpleNamespace(
            radar_controller=controller,
            _last_radar_frame_index=None,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_start_button=start_button,
            radar_stop_button=stop_button,
            radar_calibrate_button=calibrate_button,
            after=lambda _delay, _callback: None,
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)

        self.assertIs(gui.radar_controller, controller)
        self.assertEqual(start_button.state, "disabled")
        self.assertEqual(stop_button.state, "normal")
        self.assertEqual(calibrate_button.state, "normal")
        self.assertIn("等待同步", radar_status.value)
        self.assertNotIn("清理失败", radar_status.value)

    def test_runtime_diagnostics_explain_signal_sync_and_recovery_state(self):
        source = SimpleNamespace(
            session_attempt=3,
            phase="capturing",
            tx_uploaded=True,
            capture_count=12,
            rx_samples=552960,
            rx_rms_dbfs=-49.92,
            rx_peak_dbfs=-38.62,
            clip_ratio=0.0004,
        )
        sync = SimpleNamespace(
            failed_metric="min_correlation",
            min_correlation=0.031,
            mean_correlation=0.067,
            periodic_coherence=0.004,
            idle_to_active_db=-1.8,
            min_correlation_threshold=0.05,
            mean_correlation_threshold=0.08,
            periodic_coherence_threshold=0.0075,
        )
        status = SimpleNamespace(
            processing_error="chirp correlation is below the sync threshold",
            consecutive_processing_errors=16,
            source_recoveries=2,
            max_source_recoveries=5,
            recovery_exhausted=False,
            source_diagnostics=source,
            sync_diagnostics=sync,
        )

        lines = format_radar_runtime_diagnostics(status)
        rendered = "\n".join(lines)

        self.assertIn("会话 3", rendered)
        self.assertIn("capturing", rendered)
        self.assertIn("TX循环已上传", rendered)
        self.assertIn("-49.92 dBFS", rendered)
        self.assertIn("0.0310 / 0.0500", rendered)
        self.assertIn("恢复 2/5", rendered)
        self.assertIn("连续同步失败 16", rendered)

    def test_runtime_diagnostics_marks_exhausted_recovery_and_missing_signal(self):
        status = SimpleNamespace(
            processing_error="sync failed",
            consecutive_processing_errors=48,
            source_recoveries=5,
            max_source_recoveries=5,
            recovery_exhausted=True,
            source_diagnostics=None,
            sync_diagnostics=None,
        )

        rendered = "\n".join(format_radar_runtime_diagnostics(status))

        self.assertIn("尚无RX采集统计", rendered)
        self.assertIn("恢复已耗尽", rendered)
        self.assertIn("sync failed", rendered)

    def test_runtime_log_is_bounded_to_recent_two_hundred_lines(self):
        class TextBuffer:
            def __init__(self):
                self.value = ""

            def configure(self, **_kwargs):
                pass

            def delete(self, *_args):
                self.value = ""

            def insert(self, _where, value):
                self.value += value

            def see(self, _where):
                pass

        gui = SimpleNamespace(_radar_log_lines=[], radar_log_text=TextBuffer())

        SdrVideoGui._append_radar_log(gui, [f"line-{index}" for index in range(205)])

        self.assertEqual(len(gui._radar_log_lines), 200)
        self.assertNotIn("line-0\n", gui.radar_log_text.value)
        self.assertIn("line-204", gui.radar_log_text.value)

    def test_poll_prioritizes_shutdown_timeout_over_sync_error(self):
        status = SimpleNamespace(
            running=True,
            cleanup_complete=False,
            error="chirp correlation is below the sync threshold",
            shutdown_error="radar worker did not stop before timeout",
            overruns=0,
        )
        controller = SimpleNamespace(status=lambda: status, latest_frame=lambda: None)
        start_button = SimpleNamespace(state=None)
        stop_button = SimpleNamespace(state=None)
        start_button.configure = lambda *, state: setattr(start_button, "state", state)
        stop_button.configure = lambda *, state: setattr(stop_button, "state", state)
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        gui = SimpleNamespace(
            radar_controller=controller,
            _last_radar_frame_index=None,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_start_button=start_button,
            radar_stop_button=stop_button,
            after=lambda _delay, _callback: None,
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)

        self.assertIn("停止中", radar_status.value)
        self.assertNotIn("等待同步", radar_status.value)
        self.assertEqual(start_button.state, "disabled")
        self.assertEqual(stop_button.state, "disabled")

    def test_calibration_timeout_remains_visible_during_sync_errors(self):
        calibration = SimpleNamespace(
            active=True,
            collected_cpis=0,
            required_cpis=16,
            ready=False,
            error=None,
        )

        class Processor:
            def background_calibration_status(self):
                return calibration

            def cancel_background_calibration(self, reason):
                calibration.active = False
                calibration.error = reason
                return True

        status = SimpleNamespace(
            running=True,
            cleanup_complete=False,
            error="chirp correlation is below the sync threshold",
            shutdown_error=None,
            overruns=0,
        )
        controller = SimpleNamespace(
            processor=Processor(),
            status=lambda: status,
            latest_frame=lambda: None,
        )
        radar_status = SimpleNamespace(value="")
        radar_status.set = lambda value: setattr(radar_status, "value", value)
        start_button = SimpleNamespace(state=None)
        stop_button = SimpleNamespace(state=None)
        calibrate_button = SimpleNamespace(state=None)
        for button in (start_button, stop_button, calibrate_button):
            button.configure = lambda *, state, target=button: setattr(
                target, "state", state
            )
        gui = SimpleNamespace(
            radar_controller=controller,
            _last_radar_frame_index=None,
            _radar_calibration_deadline=0.0,
            radar_source_label="E310",
            radar_status_var=radar_status,
            radar_start_button=start_button,
            radar_stop_button=stop_button,
            radar_calibrate_button=calibrate_button,
            after=lambda _delay, _callback: None,
            poll_radar_controller=lambda: None,
        )

        SdrVideoGui.poll_radar_controller(gui)

        self.assertIn("空场标定未应用", radar_status.value)
        self.assertIn("等待同步", radar_status.value)
        self.assertEqual(calibrate_button.state, "normal")

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

    def test_gui_e310_start_passes_verified_radio_settings_and_ignores_target_fields(self):
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

        value = Value
        gui = SimpleNamespace(
            radar_controller=None,
            radar_source_var=value("E310"),
            radar_replay_path_var=value(""),
            radar_carrier_ghz_var=value("76"),
            radar_sample_rate_msps_var=value("30"),
            radar_bandwidth_mhz_var=value("20"),
            radar_active_us_var=value("128"),
            radar_idle_us_var=value("16"),
            radar_chirp_count_var=value("64"),
            radar_cfar_db_var=value("12"),
            radar_display_range_var=value("50"),
            radar_tx_channel_var=value("0"),
            radar_rx_channel_var=value("0"),
            radar_tx_gain_var=value("-20"),
            radar_rx_gain_var=value("20"),
            radar_tx_amplitude_var=value("0.40"),
            radar_target_range_var=value("not-a-number"),
            radar_target_velocity_var=value("not-a-number"),
            radar_target_snr_var=value("not-a-number"),
            radar_status_var=value(""),
            radar_derived_var=value(""),
            radar_start_button=Widget(),
            radar_stop_button=Widget(),
            radar_tab=object(),
            notebook=SimpleNamespace(select=lambda _tab: None),
        )
        gui.build_radar_config = lambda: SdrVideoGui.build_radar_config(gui)
        controller = SimpleNamespace(start=lambda: None)

        with patch(
            "scripts.sdr_video_gui.create_radar_runtime",
            return_value=(controller, gui.build_radar_config()),
        ) as create_runtime, patch("scripts.sdr_video_gui.messagebox.showerror") as showerror:
            SdrVideoGui.start_radar(gui)

        showerror.assert_not_called()
        args = create_runtime.call_args.args
        self.assertEqual(args[:4], ("E310", gui.build_radar_config(), None, None))
        radio_config = args[4]
        self.assertEqual(radio_config.tx_port, "B")
        self.assertEqual(radio_config.rx_port, "B_BALANCED")
        self.assertEqual(radio_config.tx_channel, 0)
        self.assertEqual(radio_config.rx_channel, 0)
        self.assertEqual(radio_config.tx_gain_db, -20.0)
        self.assertEqual(radio_config.rx_gain_db, 20.0)
        self.assertEqual(radio_config.tx_amplitude, 0.4)

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

    def test_direct_gui_script_bootstraps_package_imports_without_tk(self):
        project = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["FMCW_GUI_IMPORT_ONLY"] = "1"

        result = subprocess.run(
            [sys.executable, "scripts/sdr_video_gui.py"],
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
