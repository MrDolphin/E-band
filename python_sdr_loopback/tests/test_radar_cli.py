import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sdr_loopback.radar.config import RadarConfig


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
        self.assertLessEqual(abs(detected["range_m"] - 22.5), config.range_resolution_m)
        self.assertLessEqual(
            abs(detected["radial_velocity_mps"] - 1.2),
            config.velocity_resolution_mps,
        )

    def test_radar_runner_processes_a_finite_synthetic_frame(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            completed = self.run_script(
                "run_fmcw_radar.py",
                "--output-dir", output,
                "--frames", "1",
                "--target", "22.5,1.2,18",
                "--seed", "7",
                *REDUCED_CONFIG_FLAGS,
            )
            metrics = (output / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
            result_exists = (output / "result.json").is_file()

        values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
        self.assertEqual(values["frames_processed"], "1")
        self.assertEqual(len(metrics), 1)
        self.assertTrue(result_exists)


if __name__ == "__main__":
    unittest.main()
