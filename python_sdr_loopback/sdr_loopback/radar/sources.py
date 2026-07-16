"""Interchangeable finite-CPI data sources for the FMCW processor."""

from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from threading import Lock
import time
from typing import Protocol

import numpy as np

from .config import RadarConfig
from .models import RadarCapture, SyntheticTarget
from .simulator import simulate_capture
from .storage import load_capture
from .waveform import generate_cpi


class RadarDataSource(Protocol):
    def open(self) -> None: ...

    def capture(self) -> RadarCapture: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class E310RadioConfig:
    uri: str = "ip:192.168.1.10"
    lo_hz: int = 900_000_000
    rx_channel: int = 0
    tx_channel: int = 0
    rx_port: str = "B_BALANCED"
    tx_port: str = "B"
    rx_gain_db: float = 20.0
    tx_gain_db: float = -40.0
    dac_peak: float = 8191.0
    adc_full_scale: float = 2048.0
    tx_amplitude: float = 0.25
    pre_tx_settle_s: float = 1.0
    settle_s: float = 0.25
    sync_margin_chirps: int = 1

    def __post_init__(self) -> None:
        integer_fields = (
            ("lo_hz", self.lo_hz, 1, None),
            ("rx_channel", self.rx_channel, 0, 1),
            ("tx_channel", self.tx_channel, 0, 1),
            ("sync_margin_chirps", self.sync_margin_chirps, 0, None),
        )
        for name, value, minimum, maximum in integer_fields:
            if (
                not isinstance(value, (int, np.integer))
                or isinstance(value, (bool, np.bool_))
                or value < minimum
                or (maximum is not None and value > maximum)
            ):
                raise ValueError(f"{name} must be an integer in the supported range")
        if self.rx_channel not in (0, 1) or self.tx_channel not in (0, 1):
            raise ValueError("E310 channels must be 0 or 1")
        finite_fields = (
            ("rx_gain_db", self.rx_gain_db),
            ("tx_gain_db", self.tx_gain_db),
            ("dac_peak", self.dac_peak),
            ("adc_full_scale", self.adc_full_scale),
            ("tx_amplitude", self.tx_amplitude),
            ("pre_tx_settle_s", self.pre_tx_settle_s),
            ("settle_s", self.settle_s),
        )
        for name, value in finite_fields:
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"{name} must be a finite number")
            try:
                finite = math.isfinite(value)
            except TypeError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be finite")
        if not (
            -89.75 <= self.tx_gain_db <= -15.0
            and -3.0 <= self.rx_gain_db <= 73.0
            and 0.0 < self.tx_amplitude <= 0.4
        ):
            raise ValueError(
                "E310 gain and amplitude exceed the safe hardware range"
            )
        if (
            self.dac_peak <= 0.0
            or self.adc_full_scale <= 0.0
            or self.pre_tx_settle_s < 0.0
            or self.settle_s < 0.0
        ):
            raise ValueError("DAC/ADC scales must be positive and settle times nonnegative")

    def sync_margin_samples(self, config: RadarConfig) -> int:
        return self.sync_margin_chirps * config.samples_per_chirp


class E310CpiSource:
    """Acquire finite FMCW CPIs from an AD9361-backed ANTSDR E310."""

    _TX_MUTE_GAIN_DB = -89.75

    def __init__(
        self,
        config: RadarConfig,
        radio_config: E310RadioConfig,
        adi_module: object | None = None,
    ) -> None:
        self.config = config
        self.radio_config = radio_config
        self._adi_module = adi_module
        self._sdr = None
        self._tx_iq = self._dac_samples(generate_cpi(config))
        self._tx_uploaded = False
        self._operation_lock = Lock()

    def _dac_samples(self, normalized: np.ndarray) -> np.ndarray:
        values = np.asarray(normalized, dtype=np.complex64)
        if values.ndim != 1 or not np.all(np.isfinite(values)):
            raise ValueError("DAC payload must be one-dimensional and finite")
        component_peak = max(
            float(np.max(np.abs(values.real))),
            float(np.max(np.abs(values.imag))),
            np.finfo(float).tiny,
        )
        scale = self.radio_config.dac_peak * self.radio_config.tx_amplitude / component_peak
        scaled = values * scale
        real = np.clip(scaled.real, -self.radio_config.dac_peak, self.radio_config.dac_peak)
        imag = np.clip(scaled.imag, -self.radio_config.dac_peak, self.radio_config.dac_peak)
        payload = (real + 1j * imag).astype(np.complex64)
        if not np.all(np.isfinite(payload)):
            raise ValueError("DAC payload must remain finite after scaling")
        return payload

    @staticmethod
    def _destroy_buffers(sdr: object) -> list[tuple[str, Exception]]:
        errors: list[tuple[str, Exception]] = []
        for method_name in ("tx_destroy_buffer", "rx_destroy_buffer"):
            method = getattr(sdr, method_name, None)
            if method is not None:
                try:
                    method()
                except Exception as error:
                    errors.append((method_name, error))
        return errors

    def _mute_tx(self, sdr: object) -> list[tuple[str, Exception]]:
        errors: list[tuple[str, Exception]] = []
        try:
            sdr._set_iio_attr_float(
                f"voltage{self.radio_config.tx_channel}",
                "hardwaregain",
                True,
                self._TX_MUTE_GAIN_DB,
            )
        except Exception as error:
            errors.append(("tx_hardwaregain_mute", error))
        disable_dds = getattr(sdr, "disable_dds", None)
        if disable_dds is not None:
            try:
                disable_dds()
            except Exception as error:
                errors.append(("disable_dds", error))
        return errors

    def _cleanup_hardware(self, sdr: object) -> list[tuple[str, Exception]]:
        return [*self._mute_tx(sdr), *self._destroy_buffers(sdr)]

    @staticmethod
    def _cleanup_failure(errors: list[tuple[str, Exception]]) -> RuntimeError:
        details = "; ".join(f"{action}: {error}" for action, error in errors)
        return RuntimeError(f"E310 cleanup failed: {details}")

    def _upload_tx(self) -> None:
        self._sdr.tx_cyclic_buffer = True
        self._sdr.tx(self._tx_iq)
        self._tx_uploaded = True
        if self.radio_config.settle_s:
            time.sleep(self.radio_config.settle_s)

    def open(self) -> None:
        if self._sdr is not None:
            return
        adi_module = self._adi_module
        if adi_module is None:
            try:
                import adi as adi_module
            except ImportError as error:
                raise RuntimeError("pyadi-iio is required for E310 acquisition") from error
        radio = self.radio_config
        sdr = adi_module.ad9361(uri=radio.uri)
        self._sdr = sdr
        try:
            sdr.sample_rate = int(self.config.sample_rate_hz)
            sdr.rx_rf_bandwidth = int(self.config.bandwidth_hz)
            sdr.tx_rf_bandwidth = int(self.config.bandwidth_hz)
            sdr.rx_lo = radio.lo_hz
            sdr.tx_lo = radio.lo_hz
            sdr.rx_enabled_channels = [radio.rx_channel]
            sdr.tx_enabled_channels = [radio.tx_channel]
            sdr.rx_buffer_size = self.config.cpi_samples + radio.sync_margin_samples(self.config)
            sdr._set_iio_attr(f"voltage{radio.rx_channel}", "rf_port_select", False, radio.rx_port)
            sdr._set_iio_attr(f"voltage{radio.tx_channel}", "rf_port_select", True, radio.tx_port)
            sdr._set_iio_attr(f"voltage{radio.rx_channel}", "gain_control_mode", False, "manual")
            sdr._set_iio_attr_float(f"voltage{radio.rx_channel}", "hardwaregain", False, radio.rx_gain_db)
            sdr._set_iio_attr_float(f"voltage{radio.tx_channel}", "hardwaregain", True, radio.tx_gain_db)
            if radio.pre_tx_settle_s:
                time.sleep(radio.pre_tx_settle_s)
            self._upload_tx()
        except BaseException as error:
            cleanup_errors = self._cleanup_hardware(sdr)
            self._tx_uploaded = False
            if not cleanup_errors:
                self._sdr = None
            elif hasattr(error, "add_note"):
                error.add_note(str(self._cleanup_failure(cleanup_errors)))
            raise

    def capture(self) -> RadarCapture:
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("E310 operation already in progress")
        try:
            return self._capture_once()
        finally:
            self._operation_lock.release()

    def _capture_once(self) -> RadarCapture:
        if self._sdr is None:
            raise RuntimeError("radar data source is not open")
        sdr = self._sdr
        try:
            if not self._tx_uploaded:
                self._upload_tx()
            raw = sdr.rx()
            rx_iq = np.asarray(
                raw[0] if isinstance(raw, list) else raw, dtype=np.complex64
            ) / np.float32(self.radio_config.adc_full_scale)
            minimum = self.config.cpi_samples + self.radio_config.sync_margin_samples(self.config)
            if rx_iq.ndim != 1 or rx_iq.size < minimum:
                raise ValueError(f"E310 RX returned fewer than {minimum} samples")
            if not np.all(np.isfinite(rx_iq)):
                raise ValueError("E310 RX returned non-finite IQ samples")
            return RadarCapture(
                timestamp=time.time(),
                config=self.config,
                tx_iq=generate_cpi(self.config),
                rx_iq=rx_iq,
                # Cyclic TX and RX DMA start asynchronously; the processor must
                # correlate within the retained margin instead of assuming an offset.
                chirp_start_sample=0,
            )
        except Exception as error:
            cleanup_errors = self._cleanup_hardware(sdr)
            self._tx_uploaded = False
            if not cleanup_errors:
                self._sdr = None
            elif hasattr(error, "add_note"):
                error.add_note(str(self._cleanup_failure(cleanup_errors)))
            raise

    def close(self) -> None:
        with self._operation_lock:
            self._close_once()

    def _close_once(self) -> None:
        sdr = self._sdr
        if sdr is None:
            return
        cleanup_errors = self._cleanup_hardware(sdr)
        self._tx_uploaded = False
        if cleanup_errors:
            raise self._cleanup_failure(cleanup_errors) from cleanup_errors[0][1]
        self._sdr = None


class SyntheticTargetSource:
    """Generate one deterministic synthetic CPI per capture call."""

    def __init__(
        self,
        config: RadarConfig,
        targets: Sequence[SyntheticTarget] = (),
        *,
        seed: int = 0,
    ) -> None:
        self.config = config
        self.targets = tuple(targets)
        self.seed = seed
        self.frame_index = 0
        self._is_open = False

    def open(self) -> None:
        self.frame_index = 0
        self._is_open = True

    def capture(self) -> RadarCapture:
        if not self._is_open:
            raise RuntimeError("radar data source is not open")
        frame_index = self.frame_index
        capture = simulate_capture(
            self.config,
            self.targets,
            timestamp=float(frame_index),
            seed=self.seed + frame_index,
        )
        self.frame_index += 1
        return capture

    def close(self) -> None:
        self._is_open = False


class IqReplaySource:
    """Replay capture archives in order, optionally looping at the end."""

    def __init__(self, paths: Sequence[str | Path], *, loop: bool = False) -> None:
        self.paths = tuple(Path(path) for path in paths)
        self.loop = loop
        self._index = 0
        self._is_open = False

    def open(self) -> None:
        self._index = 0
        self._is_open = True

    def capture(self) -> RadarCapture:
        if not self._is_open:
            raise RuntimeError("radar data source is not open")
        if not self.paths:
            raise StopIteration("no replay captures configured")
        if self._index >= len(self.paths):
            if not self.loop:
                raise StopIteration("replay captures exhausted")
            self._index = 0
        path = self.paths[self._index]
        capture = load_capture(path)
        self._index += 1
        return capture

    def close(self) -> None:
        self._is_open = False
