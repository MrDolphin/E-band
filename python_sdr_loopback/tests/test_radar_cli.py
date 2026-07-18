import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.simulator import simulate_capture
from sdr_loopback.radar.storage import save_capture
from scripts import run_fmcw_radar


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REDUCED_CONFIG_FLAGS = (
    "--sample-rate-hz", "2000000",
    "--active-time-us", "64",
    "--idle-time-us", "16",
    "--chirp-count", "16",
    "--range-fft-size", "128",
    "--doppler-fft-size", "16",
)


class RadarCliTests(unittest.TestCase):
    def run_script(self, script_name, *arguments):
        return subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / script_name), *map(str, arguments)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_fixture_generation_and_analysis_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            capture_path = output / "single_target.npz"
            generated = self.run_script(
                "generate_fmcw_fixture.py",
                "--output", capture_path,
                "--target", "22.5,1.2,18",
                "--seed", "7",
                *REDUCED_CONFIG_FLAGS,
            )
            result_directory = output / "result"
            analyzed = self.run_script(
                "analyze_fmcw_capture.py",
                capture_path,
                "--output-dir", result_directory,
            )
            result = json.loads(
                (result_directory / "result.json").read_text(encoding="utf-8")
            )

        generated_values = dict(line.split("=", 1) for line in generated.stdout.splitlines())
        analyzed_values = dict(line.split("=", 1) for line in analyzed.stdout.splitlines())
        self.assertEqual(generated_values["active_samples"], "128")
        self.assertEqual(generated_values["chirp_count"], "16")
        self.assertEqual(generated_values["truth_targets"], "1")
        self.assertEqual(analyzed_values["targets_detected"], "1")
        self.assertEqual(analyzed_values["azimuth_measured"], "false")

        config = RadarConfig(**result["radar_config"])
        detected = result["targets"][0]
        self.assertEqual(float(analyzed_values["detected_range_m"]), detected["range_m"])
        self.assertEqual(
            float(analyzed_values["detected_velocity_mps"]),
            detected["radial_velocity_mps"],
        )
        self.assertLessEqual(abs(detected["range_m"] - 22.5), config.range_resolution_m)
        self.assertLessEqual(
            abs(detected["radial_velocity_mps"] - 1.2),
            config.velocity_resolution_mps,
        )

    def test_radar_runner_processes_a_finite_synthetic_frame(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            captures = output / "captures"
            completed = self.run_script(
                "run_fmcw_radar.py",
                "--output-dir", output,
                "--capture-dir", captures,
                "--frames", "1",
                "--target", "22.5,1.2,18",
                "--seed", "7",
                *REDUCED_CONFIG_FLAGS,
            )
            metrics = (output / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
            result_exists = (output / "result.json").is_file()
            capture_exists = (captures / "capture-000000.npz").is_file()

        values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
        self.assertEqual(values["frames_processed"], "1")
        self.assertEqual(len(metrics), 1)
        self.assertTrue(result_exists)
        self.assertTrue(capture_exists)

    def test_headless_synthetic_soak_writes_metrics_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            metrics_path = Path(temporary_directory) / "metrics.jsonl"
            completed = self.run_script(
                "run_fmcw_radar.py",
                "--headless",
                "--metrics", metrics_path,
                "--duration-sec", "0.05",
                "--target", "22.5,0,18",
                "--seed", "7",
                *REDUCED_CONFIG_FLAGS,
            )
            summary = json.loads(
                metrics_path.with_name("soak_summary.json").read_text(encoding="utf-8")
            )
            metric_count = len(metrics_path.read_text(encoding="utf-8").splitlines())

        values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
        self.assertGreater(metric_count, 0)
        self.assertEqual(summary["frames_processed"], metric_count)
        self.assertEqual(values["frames_failed"], "0")
        self.assertEqual(values["queue_max_depth"], "1")
        self.assertEqual(values["controller_stopped"], "true")
        self.assertEqual(values["soak_ok"], "true")

    def test_runner_derives_fft_size_for_56_mhz_profile(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            metrics_path = Path(temporary_directory) / "metrics.jsonl"
            completed = self.run_script(
                "run_fmcw_radar.py",
                "--headless",
                "--metrics", metrics_path,
                "--sample-rate-hz", "61440000",
                "--bandwidth-hz", "56000000",
                "--active-time-us", "125",
                "--idle-time-us", "15.625",
                "--chirp-count", "64",
            )

        self.assertIn("frames_processed=1", completed.stdout)

    def test_e310_dry_run_reports_explicit_radio_levels(self):
        completed = self.run_script(
            "run_fmcw_radar.py",
            "--source", "e310",
            "--dry-run",
            "--tx-gain-db", "-30",
            "--tx-amplitude", "0.4",
            "--rx-gain-db", "20",
        )

        values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
        self.assertEqual(values["tx_gain_db"], "-30.0")
        self.assertEqual(values["tx_amplitude"], "0.4")
        self.assertEqual(values["rx_gain_db"], "20.0")

    def test_sync_diagnosis_dry_run_reports_capture_stabilization(self):
        completed = self.run_script(
            "diagnose_fmcw_sync.py",
            "--dry-run",
            "--frames", "10",
            "--sample-rate-hz", "2000000",
            "--bandwidth-hz", "1000000",
            "--active-time-us", "64",
            "--idle-time-us", "16",
            "--chirp-count", "16",
            "--startup-rx-discard-buffers", "3",
        )

        values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
        self.assertEqual(values["frames"], "10")
        self.assertEqual(values["sample_rate_hz"], "2000000")
        self.assertEqual(values["startup_rx_discard_buffers"], "3")
        self.assertEqual(values["hardware_access"], "false")

    def test_e310_open_failure_still_calls_close(self):
        calls = []

        class Source:
            def __init__(self, _config, _radio):
                pass

            def open(self):
                calls.append("open")
                raise OSError("open failed")

            def close(self):
                calls.append("close")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            sys,
            "argv",
            [
                "run_fmcw_radar.py",
                "--source",
                "e310",
                "--output-dir",
                directory,
            ],
        ), patch.object(run_fmcw_radar, "E310CpiSource", Source):
            with self.assertRaisesRegex(OSError, "open failed"):
                run_fmcw_radar.main()

        self.assertEqual(calls, ["open", "close"])

    def test_synthetic_mode_ignores_e310_only_level_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = self.run_script(
                "run_fmcw_radar.py",
                "--output-dir", directory,
                "--frames", "1",
                "--target", "22.5,0,18",
                "--tx-gain-db", "0",
                "--tx-amplitude", "1",
                "--rx-gain-db", "100",
                *REDUCED_CONFIG_FLAGS,
            )

        self.assertIn("frames_processed=1", completed.stdout)

    def test_replay_order_heterogeneous_configs_loop_and_exhaustion(self):
        configs = (
            RadarConfig(
                sample_rate_hz=2e6,
                active_time_s=64e-6,
                idle_time_s=16e-6,
                chirp_count=16,
                range_fft_size=128,
                doppler_fft_size=16,
            ),
            RadarConfig(
                sample_rate_hz=2e6,
                bandwidth_hz=10e6,
                active_time_s=64e-6,
                idle_time_s=16e-6,
                chirp_count=16,
                range_fft_size=128,
                doppler_fft_size=16,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = tuple(root / f"capture_{index}.npz" for index in range(2))
            for path, config, timestamp in zip(paths, configs, (1.0, 2.0)):
                save_capture(path, simulate_capture(config, timestamp=timestamp))
            cases = (
                ("finite_exhaustion", (), 2, 2.0, 10e6),
                ("explicit_loop", ("--loop-replay",), 3, 1.0, 20e6),
            )
            for name, loop_flags, expected_count, timestamp, bandwidth_hz in cases:
                with self.subTest(name=name):
                    output = root / name
                    completed = self.run_script(
                        "run_fmcw_radar.py",
                        "--output-dir", output,
                        "--frames", "3",
                        "--replay", paths[0],
                        "--replay", paths[1],
                        *loop_flags,
                    )
                    result = json.loads(
                        (output / "result.json").read_text(encoding="utf-8")
                    )
                    metrics = (output / "metrics.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                    values = dict(
                        line.split("=", 1) for line in completed.stdout.splitlines()
                    )

                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(values["frames_processed"], str(expected_count))
                    self.assertEqual(result["timestamp"], timestamp)
                    self.assertEqual(
                        result["radar_config"]["bandwidth_hz"], bandwidth_hz
                    )
                    self.assertEqual(len(metrics), expected_count)


if __name__ == "__main__":
    unittest.main()
