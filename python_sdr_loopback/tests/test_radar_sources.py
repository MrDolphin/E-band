import unittest
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from sdr_loopback.radar.calibration import BackgroundCalibration
from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import RadarCapture
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig
from sdr_loopback.radar.waveform import generate_cpi
from scripts.diagnose_e310_tx import FmcwTransmitter


class FakeAd9361:
    def __init__(self, uri, *, fail_rx=False):
        self.uri = uri
        self.fail_rx = fail_rx
        self.attrs = []
        self.float_attrs = []
        self.events = []
        self.rx_calls = 0
        self.tx_payloads = []
        self.tx_destroy_count = 0
        self.rx_destroy_count = 0
        self.dds_disable_count = 0
        self.tx_calls = 0
        self.fail_tx_call = None

    def _set_iio_attr(self, channel, name, output, value):
        self.attrs.append((channel, name, output, value))

    def _set_iio_attr_float(self, channel, name, output, value):
        self.float_attrs.append((channel, name, output, value))
        self.events.append(("float_attr", channel, name, output, value))

    def tx(self, samples):
        self.tx_calls += 1
        if self.tx_calls == self.fail_tx_call:
            raise OSError("iio transmit failed")
        self.tx_payloads.append(np.asarray(samples).copy())

    def rx(self):
        self.rx_calls += 1
        if self.fail_rx:
            raise OSError("iio receive failed")
        return np.arange(self.rx_buffer_size, dtype=np.float32).astype(np.complex64)

    def tx_destroy_buffer(self):
        self.tx_destroy_count += 1
        self.events.append(("tx_destroy",))

    def rx_destroy_buffer(self):
        self.rx_destroy_count += 1
        self.events.append(("rx_destroy",))

    def disable_dds(self):
        self.dds_disable_count += 1
        self.events.append(("disable_dds",))


class FakeAdi:
    def __init__(self, *, fail_rx=False):
        self.fail_rx = fail_rx
        self.devices = []

    def ad9361(self, uri):
        device = FakeAd9361(uri, fail_rx=self.fail_rx)
        self.devices.append(device)
        return device


class E310CpiSourceTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig()
        self.radio = E310RadioConfig(
            pre_tx_settle_s=0.0,
            settle_s=0.0,
            startup_rx_discard_buffers=0,
        )

    def test_radio_config_uses_hardware_verified_startup_wait(self):
        self.assertEqual(E310RadioConfig().pre_tx_settle_s, 1.0)

    def test_open_discards_startup_rx_buffers_after_enabling_cyclic_tx(self):
        radio = E310RadioConfig(
            pre_tx_settle_s=0.0,
            settle_s=0.0,
            startup_rx_discard_buffers=2,
        )
        adi = FakeAdi()
        source = E310CpiSource(self.config, radio, adi_module=adi)

        source.open()

        device = adi.devices[0]
        self.assertEqual(device.rx_calls, 2)
        self.assertEqual(device.tx_calls, 1)

    def test_radio_config_rejects_levels_outside_safe_hardware_envelope(self):
        invalid = (
            dict(tx_gain_db=-14.99),
            dict(tx_gain_db=-90.0),
            dict(tx_amplitude=0.401),
            dict(rx_gain_db=-3.01),
            dict(rx_gain_db=73.01),
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "safe hardware range"):
                    E310RadioConfig(**values)

    def test_open_configures_radio_and_uploads_bounded_cpi(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)

        source.open()
        device = adi.devices[0]

        self.assertEqual(device.uri, "ip:192.168.1.10")
        self.assertEqual(device.sample_rate, 30_000_000)
        self.assertEqual(device.rx_rf_bandwidth, 20_000_000)
        self.assertEqual(device.tx_rf_bandwidth, 20_000_000)
        self.assertEqual(device.rx_lo, 900_000_000)
        self.assertEqual(device.tx_lo, 900_000_000)
        self.assertEqual(device.rx_enabled_channels, [0])
        self.assertEqual(device.tx_enabled_channels, [0])
        self.assertGreaterEqual(device.rx_buffer_size, self.config.cpi_samples)
        self.assertTrue(device.tx_cyclic_buffer)
        self.assertIn(("voltage0", "rf_port_select", False, "B_BALANCED"), device.attrs)
        self.assertIn(("voltage0", "rf_port_select", True, "B"), device.attrs)
        self.assertIn(("voltage0", "gain_control_mode", False, "manual"), device.attrs)
        self.assertIn(("voltage0", "hardwaregain", False, 20.0), device.float_attrs)
        self.assertIn(("voltage0", "hardwaregain", True, -40.0), device.float_attrs)
        self.assertEqual(device.tx_payloads[0].shape, (self.config.cpi_samples,))
        self.assertLessEqual(float(np.max(np.abs(device.tx_payloads[0].real))), self.radio.dac_peak)
        self.assertLessEqual(float(np.max(np.abs(device.tx_payloads[0].imag))), self.radio.dac_peak)

    def test_open_waits_for_radio_before_first_tx_upload(self):
        radio = E310RadioConfig(pre_tx_settle_s=0.5, settle_s=0.0)
        source = E310CpiSource(self.config, radio, adi_module=FakeAdi())

        with patch("sdr_loopback.radar.sources.time.sleep") as sleep:
            source.open()

        sleep.assert_called_once_with(0.5)

    def test_capture_reuses_buffers_on_success_and_cleans_up_on_error(self):
        for fail_rx in (False, True):
            with self.subTest(fail_rx=fail_rx):
                adi = FakeAdi(fail_rx=fail_rx)
                source = E310CpiSource(self.config, self.radio, adi_module=adi)
                source.open()
                device = adi.devices[0]

                if fail_rx:
                    with self.assertRaisesRegex(OSError, "iio receive"):
                        source.capture()
                else:
                    capture = source.capture()
                    self.assertEqual(capture.rx_iq.dtype, np.complex64)
                    self.assertEqual(capture.rx_iq.size, device.rx_buffer_size)
                    self.assertEqual(capture.rx_iq[2048], 1.0 + 0.0j)
                    self.assertEqual(capture.chirp_start_sample, 0)
                    self.assertEqual(
                        capture.rx_iq.size - self.config.cpi_samples,
                        self.radio.sync_margin_samples(self.config),
                    )
                    source.capture()
                    self.assertEqual(device.tx_calls, 1)
                    self.assertEqual(device.tx_destroy_count, 0)
                    self.assertEqual(device.rx_destroy_count, 0)
                if fail_rx:
                    self.assertGreaterEqual(device.tx_destroy_count, 1)
                    self.assertGreaterEqual(device.rx_destroy_count, 1)
                    mute = device.events.index(
                        ("float_attr", "voltage0", "hardwaregain", True, -89.75)
                    )
                    self.assertLess(mute, device.events.index(("tx_destroy",)))
                source.close()

    def test_concurrent_buffer_operations_are_rejected_during_capture(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)
        source.open()
        device = adi.devices[0]
        entered = Event()
        release = Event()
        original_rx = device.rx

        def blocking_rx():
            entered.set()
            release.wait(timeout=2.0)
            return original_rx()

        device.rx = blocking_rx
        worker = Thread(target=source.capture)
        worker.start()
        self.assertTrue(entered.wait(timeout=1.0))
        try:
            with self.assertRaisesRegex(RuntimeError, "already in progress"):
                source.capture()
        finally:
            release.set()
            worker.join(timeout=2.0)
            source.close()
        self.assertFalse(worker.is_alive())

    def test_close_waits_for_capture_then_mutes_and_destroys_buffers(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)
        source.open()
        device = adi.devices[0]
        entered = Event()
        release = Event()
        original_rx = device.rx
        close_errors = []

        def blocking_rx():
            entered.set()
            release.wait(timeout=2.0)
            return original_rx()

        def close_source():
            try:
                source.close()
            except Exception as error:
                close_errors.append(error)

        device.rx = blocking_rx
        capture_worker = Thread(target=source.capture)
        close_worker = Thread(target=close_source)
        capture_worker.start()
        self.assertTrue(entered.wait(timeout=1.0))
        close_worker.start()
        self.assertTrue(close_worker.is_alive())

        release.set()
        capture_worker.join(timeout=2.0)
        close_worker.join(timeout=2.0)

        self.assertEqual(close_errors, [])
        self.assertFalse(capture_worker.is_alive())
        self.assertFalse(close_worker.is_alive())
        self.assertIsNone(source._sdr)
        self.assertGreaterEqual(device.dds_disable_count, 1)
        self.assertGreaterEqual(device.tx_destroy_count, 1)
        self.assertGreaterEqual(device.rx_destroy_count, 1)

    def test_close_is_idempotent_and_clears_context(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)
        source.open()
        device = adi.devices[0]

        source.close()
        source.close()

        self.assertIsNone(source._sdr)
        self.assertGreaterEqual(device.tx_destroy_count, 1)
        self.assertGreaterEqual(device.rx_destroy_count, 1)
        self.assertGreaterEqual(device.dds_disable_count, 1)
        self.assertEqual(
            device.float_attrs[-1],
            ("voltage0", "hardwaregain", True, -89.75),
        )

    def test_close_reports_cleanup_failure_and_keeps_context_for_retry(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)
        source.open()
        device = adi.devices[0]
        destroy = device.tx_destroy_buffer

        def fail_destroy():
            raise OSError("tx destroy failed")

        device.tx_destroy_buffer = fail_destroy
        with self.assertRaisesRegex(RuntimeError, "tx_destroy_buffer"):
            source.close()
        self.assertIs(source._sdr, device)

        device.tx_destroy_buffer = destroy
        source.close()
        self.assertIsNone(source._sdr)

    def test_capture_error_terminates_session_instead_of_reusing_muted_context(self):
        adi = FakeAdi()
        source = E310CpiSource(self.config, self.radio, adi_module=adi)
        source.open()
        device = adi.devices[0]
        device.fail_rx = True
        with self.assertRaisesRegex(OSError, "iio receive"):
            source.capture()
        self.assertIsNone(source._sdr)
        with self.assertRaisesRegex(RuntimeError, "not open"):
            source.capture()

    def test_radio_config_rejects_nonfinite_and_wrong_type_values(self):
        invalid = {
            "lo_hz": (900e6, True, 0),
            "rx_channel": (0.0, False, 2),
            "tx_channel": (1.0, True, -1),
            "rx_gain_db": (np.nan, np.inf, True, "50"),
            "tx_gain_db": (np.nan, -np.inf, False, "-30"),
            "dac_peak": (np.nan, np.inf, True, 0.0),
            "adc_full_scale": (np.nan, np.inf, False, 0.0),
            "settle_s": (np.nan, np.inf, True, -0.1),
            "pre_tx_settle_s": (np.nan, np.inf, True, -0.1),
            "sync_margin_chirps": (1.0, True, np.nan, -1),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        E310RadioConfig(**{field: value})

    def test_dac_conversion_rejects_nonfinite_payload(self):
        source = E310CpiSource(self.config, self.radio, adi_module=FakeAdi())
        invalid = np.ones(32, dtype=np.complex64)
        invalid[3] = np.nan + 0j

        with self.assertRaisesRegex(ValueError, "finite"):
            source._dac_samples(invalid)

    def test_reused_fmcw_buffer_is_gated_only_by_tx_gain(self):
        device = FakeAd9361("ip:192.168.1.10")
        transmitter = FmcwTransmitter.__new__(FmcwTransmitter)
        transmitter.reuse_buffer = True
        transmitter.source = SimpleNamespace(
            _sdr=device,
            radio_config=SimpleNamespace(tx_channel=0),
            _mute_tx=lambda sdr: (
                sdr._set_iio_attr_float(
                    "voltage0", "hardwaregain", True, -89.75
                ),
                sdr.disable_dds(),
            ),
        )

        transmitter.off()

        self.assertEqual(device.dds_disable_count, 0)
        self.assertEqual(
            device.float_attrs[-1],
            ("voltage0", "hardwaregain", True, -89.75),
        )


class BackgroundCalibrationTests(unittest.TestCase):
    def test_mean_round_trip_guard_and_config_mismatch(self):
        config = RadarConfig()
        matrices = (
            np.full((config.chirp_count, config.active_samples), 1 + 2j),
            np.full((config.chirp_count, config.active_samples), 3 + 4j),
        )
        calibration = BackgroundCalibration.from_dechirped(
            config, matrices, near_range_guard_m=8.0
        )

        corrected = calibration.subtract(config, np.full_like(matrices[0], 5 + 6j))
        np.testing.assert_allclose(corrected, 3 + 3j)
        self.assertEqual(calibration.cpi_count, 2)
        self.assertEqual(calibration.config_hash, BackgroundCalibration.hash_config(config))

        with self.assertRaisesRegex(ValueError, "configuration"):
            calibration.subtract(RadarConfig(bandwidth_hz=10e6), matrices[0])

        spectrum = np.ones((config.chirp_count, config.range_fft_size // 2 + 1))
        guarded = calibration.apply_near_range_guard(config, spectrum, spectrum * 0.5)
        guard_bins = calibration.near_range_guard_bins(config)
        np.testing.assert_array_equal(guarded[:, :guard_bins], spectrum[:, :guard_bins])
        np.testing.assert_array_equal(guarded[:, guard_bins:], spectrum[:, guard_bins:] * 0.5)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "background.npz"
            calibration.save(path)
            restored = BackgroundCalibration.load(path)
        self.assertEqual(restored.config_hash, calibration.config_hash)
        self.assertEqual(restored.cpi_count, calibration.cpi_count)
        np.testing.assert_array_equal(restored.mean_dechirped, calibration.mean_dechirped)

    def test_accumulates_correlation_aligned_captures_with_different_dma_offsets(self):
        config = RadarConfig()
        tx = generate_cpi(config)
        captures = tuple(
            RadarCapture(
                timestamp=float(index),
                config=config,
                tx_iq=tx,
                rx_iq=np.concatenate(
                    (np.zeros(offset, dtype=np.complex64), tx)
                ),
                chirp_start_sample=0,
            )
            for index, offset in enumerate((13, 29))
        )

        calibration = BackgroundCalibration.from_captures(config, captures)

        self.assertEqual(calibration.cpi_count, 2)
        self.assertEqual(
            calibration.mean_dechirped.shape,
            (config.chirp_count, config.active_samples),
        )
        np.testing.assert_allclose(calibration.mean_dechirped, 0.0, atol=1e-6)

    def test_from_dechirped_accumulates_reused_stream_buffer_immediately(self):
        config = RadarConfig(chirp_count=2, doppler_fft_size=2)
        buffer = np.empty(
            (config.chirp_count, config.active_samples), dtype=np.complex64
        )

        def matrices():
            for value in (1 + 2j, 3 + 4j):
                buffer.fill(value)
                yield buffer

        calibration = BackgroundCalibration.from_dechirped(config, matrices())

        self.assertEqual(calibration.cpi_count, 2)
        np.testing.assert_allclose(calibration.mean_dechirped, 2 + 3j)

    def test_load_rejects_corrupt_calibration_metadata_and_matrix(self):
        config = RadarConfig()
        valid = {
            "config_hash": BackgroundCalibration.hash_config(config),
            "mean_dechirped": np.zeros(
                (config.chirp_count, config.active_samples), dtype=np.complex64
            ),
            "cpi_count": 2,
            "near_range_guard_m": 1.0,
        }
        corruptions = {
            "hash": {"config_hash": "not-a-sha256"},
            "rank": {"mean_dechirped": np.zeros(3, dtype=np.complex64)},
            "real": {"mean_dechirped": np.zeros((2, 3), dtype=np.float32)},
            "nonfinite": {
                "mean_dechirped": np.full((2, 3), np.nan + 0j, dtype=np.complex64)
            },
            "count": {"cpi_count": 0},
            "guard": {"near_range_guard_m": np.nan},
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, fields in corruptions.items():
                with self.subTest(name=name):
                    path = Path(directory) / f"{name}.npz"
                    np.savez_compressed(path, **(valid | fields))
                    with self.assertRaisesRegex(ValueError, "calibration"):
                        BackgroundCalibration.load(path)


class E310CliTests(unittest.TestCase):
    def test_dry_run_prints_hardware_plan_without_access(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/run_fmcw_radar.py", "--source", "e310", "--dry-run"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "source=e310",
                "uri=ip:192.168.1.10",
                "sample_rate_hz=30000000",
                "cpi_samples=276480",
                "tx_gain_db=-40.0",
                "tx_amplitude=0.25",
                "rx_gain_db=20.0",
                "hardware_access=false",
            ],
        )

    def test_tx_burst_dry_run_prints_safe_scope_plan_without_access(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_fmcw_tx_burst.py",
                "--duration-s",
                "15",
                "--dry-run",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "source=e310",
                "uri=ip:192.168.1.10",
                "tx_lo_hz=900000000",
                "tx_gain_db=-30.0",
                "tx_amplitude=0.4",
                "duration_s=15.0",
                "hardware_access=false",
            ],
        )

    def test_tx_burst_continuous_dry_run_reports_until_interrupted(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_fmcw_tx_burst.py",
                "--continuous",
                "--dry-run",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("duration_s=continuous", result.stdout.splitlines())
        self.assertIn("hardware_access=false", result.stdout.splitlines())

    def test_tx_diagnostic_dry_run_exposes_repeatable_plan(self):
        root = Path(__file__).resolve().parents[1]
        for mode in ("fmcw", "dds"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/diagnose_e310_tx.py",
                        "--mode",
                        mode,
                        "--cycles",
                        "3",
                        "--on-s",
                        "3",
                        "--off-s",
                        "3",
                        "--tx-channel",
                        "0",
                        "--tx-port",
                        "A",
                        "--dry-run",
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertIn(f"mode={mode}", lines)
                self.assertIn("cycles=3", lines)
                self.assertIn("on_s=3.0", lines)
                self.assertIn("off_s=3.0", lines)
                self.assertIn("tx_channel=0", lines)
                self.assertIn("tx_port=A", lines)
                self.assertIn("pre_upload_s=1.0", lines)
                self.assertIn(
                    f"reuse_buffer={'true' if mode == 'fmcw' else 'false'}",
                    lines,
                )
                self.assertIn("hardware_access=false", lines)

    def test_tx_diagnostic_dry_run_reports_fmcw_buffer_reuse(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "scripts/diagnose_e310_tx.py",
                "--mode",
                "fmcw",
                "--reuse-buffer",
                "--pre-upload-s",
                "1",
                "--dry-run",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reuse_buffer=true", result.stdout.splitlines())
        self.assertIn("pre_upload_s=1.0", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
