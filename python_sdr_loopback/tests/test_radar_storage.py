import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import (
    RadarDiagnostics,
    RadarFrame,
    RadarTarget,
    SyntheticTarget,
)
from sdr_loopback.radar.simulator import simulate_capture
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.storage import (
    append_metrics,
    load_capture,
    save_capture,
    save_frame,
)
from sdr_loopback.radar.sources import IqReplaySource, SyntheticTargetSource


def small_config() -> RadarConfig:
    return RadarConfig(
        carrier_hz=24e9,
        sample_rate_hz=1e6,
        bandwidth_hz=200e3,
        active_time_s=64e-6,
        idle_time_s=16e-6,
        chirp_count=8,
        range_fft_size=64,
        doppler_fft_size=8,
    )


class RadarCaptureStorageTests(unittest.TestCase):
    def test_npz_round_trip_preserves_capture_fields(self):
        config = small_config()
        target = SyntheticTarget("T01", 22.5, 1.2, 0.5, None, 18.0)
        capture = simulate_capture(
            config, (target,), timestamp=1234.5, seed=7
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "capture.npz"
            save_capture(path, capture)
            loaded = load_capture(path)

        self.assertEqual(loaded.config, config)
        self.assertEqual(loaded.timestamp, 1234.5)
        self.assertEqual(loaded.truth_targets, (target,))
        self.assertEqual(loaded.tx_iq.dtype, np.complex64)
        self.assertEqual(loaded.rx_iq.dtype, np.complex64)
        np.testing.assert_array_equal(loaded.tx_iq, capture.tx_iq)
        np.testing.assert_array_equal(loaded.rx_iq, capture.rx_iq)

    def test_load_rejects_corrupt_or_inconsistent_archives(self):
        config = small_config()
        valid = {
            "tx_iq": np.zeros(config.cpi_samples, dtype=np.complex64),
            "rx_iq": np.zeros(config.cpi_samples, dtype=np.complex64),
            "radar_config_json": json.dumps(config.__dict__),
            "capture_timestamp": 1.0,
        }
        cases = (
            ("missing", {key: value for key, value in valid.items() if key != "rx_iq"}, "missing required field: rx_iq"),
            ("invalid_json", {**valid, "radar_config_json": "{"}, "invalid radar_config_json"),
            (
                "invalid_config_value",
                {
                    **valid,
                    "radar_config_json": json.dumps(
                        {**config.__dict__, "active_time_s": 64.1e-6}
                    ),
                },
                "invalid radar_config_json",
            ),
            (
                "invalid_truth_json",
                {**valid, "truth_targets_json": "{"},
                "invalid truth_targets_json",
            ),
            (
                "invalid_truth_payload",
                {
                    **valid,
                    "truth_targets_json": json.dumps([{"unexpected": 1}]),
                },
                "invalid truth_targets_json",
            ),
            ("unequal_iq", {**valid, "rx_iq": valid["rx_iq"][:-1]}, "tx_iq and rx_iq lengths do not match"),
            (
                "config_mismatch",
                {
                    **valid,
                    "tx_iq": np.zeros(config.cpi_samples + 1, dtype=np.complex64),
                    "rx_iq": np.zeros(config.cpi_samples + 1, dtype=np.complex64),
                },
                "IQ sample count does not match radar config",
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            for name, fields, message in cases:
                with self.subTest(name=name):
                    path = Path(temporary_directory) / f"{name}.npz"
                    np.savez(path, **fields)
                    with self.assertRaisesRegex(ValueError, message):
                        load_capture(path)

    def test_frame_outputs_and_jsonl_metrics_load_independently(self):
        config = small_config()
        target = SyntheticTarget("T01", 22.5, 1.2, 0.5, None, 30.0)
        frame = FmcwProcessor(config).process(
            simulate_capture(config, (target,), timestamp=42.0, seed=7)
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            save_frame(output, frame)
            metrics_path = output / "metrics.jsonl"
            append_metrics(metrics_path, frame.diagnostics)
            append_metrics(metrics_path, frame.diagnostics)

            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            range_doppler = np.load(output / "range_doppler.npy", allow_pickle=False)
            metric_lines = metrics_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result["frame_index"], frame.frame_index)
        self.assertEqual(result["diagnostics"]["source"], "iq")
        np.testing.assert_array_equal(range_doppler, frame.range_doppler_db)
        self.assertEqual(len(metric_lines), 2)
        self.assertTrue(all(json.loads(line)["sync_ok"] for line in metric_lines))


class RadarDataSourceTests(unittest.TestCase):
    def test_synthetic_source_advances_seed_and_frame_timestamp(self):
        config = small_config()
        target = SyntheticTarget("T01", 22.5, 1.2, 0.5, None, 18.0)
        source = SyntheticTargetSource(config, (target,), seed=7)
        source.open()

        first = source.capture()
        second = source.capture()
        source.close()

        np.testing.assert_array_equal(
            first.rx_iq, simulate_capture(config, (target,), seed=7).rx_iq
        )
        np.testing.assert_array_equal(
            second.rx_iq, simulate_capture(config, (target,), seed=8).rx_iq
        )
        self.assertEqual((first.timestamp, second.timestamp), (0.0, 1.0))

    def test_replay_source_orders_captures_and_only_loops_when_requested(self):
        config = small_config()
        captures = (
            simulate_capture(config, timestamp=1.0, seed=1),
            simulate_capture(config, timestamp=2.0, seed=2),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = []
            for index, capture in enumerate(captures):
                path = Path(temporary_directory) / f"{index}.npz"
                save_capture(path, capture)
                paths.append(path)

            once = IqReplaySource(paths)
            once.open()
            self.assertEqual([once.capture().timestamp, once.capture().timestamp], [1.0, 2.0])
            with self.assertRaises(StopIteration):
                once.capture()
            once.close()

            looping = IqReplaySource(paths, loop=True)
            looping.open()
            replayed = [looping.capture().timestamp for _ in range(3)]
            looping.close()

        self.assertEqual(replayed, [1.0, 2.0, 1.0])

    def test_replay_retries_the_same_path_after_a_load_failure(self):
        config = small_config()
        with tempfile.TemporaryDirectory() as temporary_directory:
            first_path = Path(temporary_directory) / "first.npz"
            second_path = Path(temporary_directory) / "second.npz"
            np.savez(first_path, unrelated=np.zeros(1))
            save_capture(second_path, simulate_capture(config, timestamp=2.0))
            source = IqReplaySource((first_path, second_path))
            source.open()

            with self.assertRaisesRegex(ValueError, "missing required field"):
                source.capture()
            save_capture(first_path, simulate_capture(config, timestamp=1.0))
            retried = source.capture()
            source.close()

        self.assertEqual(retried.timestamp, 1.0)


if __name__ == "__main__":
    unittest.main()
