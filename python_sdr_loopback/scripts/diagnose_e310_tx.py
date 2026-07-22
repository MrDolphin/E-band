from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig


URI = "ip:192.168.1.10"
LO_HZ = 900_000_000
TX_GAIN_DB = -30.0
TX_AMPLITUDE = 0.40
DDS_OFFSET_HZ = 1_000_000
LIFECYCLE_PHASES = (
    "dds-tx-only",
    "fmcw-tx-only",
    "fmcw-rx-configured",
    "fmcw-rx-read",
)


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 < number <= 60.0:
        raise argparse.ArgumentTypeError("value must be finite and in (0, 60]")
    return number


def positive_cycles(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("cycles must be in [1, 20]")
    return number


def nonnegative_wait(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 10.0:
        raise argparse.ArgumentTypeError("wait must be finite and in [0, 10]")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run visible E310 TX diagnostics. Stop the radar GUI and custom E310 "
            "bridge first; leave only iiod running on the E310."
        ),
        epilog=(
            "Lifecycle order: "
            "--phase dds-tx-only; "
            "--phase fmcw-tx-only --reuse-buffer; "
            "--phase fmcw-rx-configured --reuse-buffer; "
            "--phase fmcw-rx-read --reuse-buffer."
        ),
    )
    diagnostic = parser.add_mutually_exclusive_group(required=True)
    diagnostic.add_argument("--mode", choices=("fmcw", "dds"))
    diagnostic.add_argument(
        "--phase",
        choices=LIFECYCLE_PHASES,
        help=(
            "Run one isolated lifecycle phase: DDS TX only, FMCW TX only, "
            "FMCW with RX configured, or FMCW with one RX read."
        ),
    )
    parser.add_argument("--cycles", type=positive_cycles, default=3)
    parser.add_argument("--on-s", type=positive_float, default=3.0)
    parser.add_argument("--off-s", type=positive_float, default=3.0)
    parser.add_argument("--tx-channel", type=int, choices=(0, 1), default=0)
    parser.add_argument("--tx-port", choices=("A", "B"), default="B")
    parser.add_argument("--pre-upload-s", type=nonnegative_wait, default=1.0)
    parser.add_argument(
        "--sample-state-s",
        type=positive_float,
        default=1.0,
        help="Seconds between structured TX state snapshots.",
    )
    buffer_mode = parser.add_mutually_exclusive_group()
    buffer_mode.add_argument(
        "--reuse-buffer",
        dest="reuse_buffer",
        action="store_true",
        help="Keep one FMCW cyclic buffer and gate output with TX gain.",
    )
    buffer_mode.add_argument(
        "--recreate-buffer",
        dest="reuse_buffer",
        action="store_false",
        help="Recreate the FMCW buffer each cycle for startup diagnostics.",
    )
    parser.set_defaults(reuse_buffer=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


class FmcwTransmitter:
    def __init__(
        self,
        tx_channel: int,
        tx_port: str,
        reuse_buffer: bool,
        pre_upload_s: float,
    ) -> None:
        self.reuse_buffer = reuse_buffer
        self.source = E310CpiSource(
            RadarConfig(),
            E310RadioConfig(
                uri=URI,
                lo_hz=LO_HZ,
                tx_channel=tx_channel,
                tx_port=tx_port,
                tx_gain_db=TX_GAIN_DB,
                tx_amplitude=TX_AMPLITUDE,
                pre_tx_settle_s=pre_upload_s,
                rx_gain_db=50.0,
            ),
        )

    def on(self) -> None:
        if self.source._sdr is None:
            self.source.open()
        else:
            self.source._sdr._set_iio_attr_float(
                f"voltage{self.source.radio_config.tx_channel}",
                "hardwaregain",
                True,
                TX_GAIN_DB,
            )
        sdr = self.source._sdr
        print(f"readback.tx_enabled_channels={sdr.tx_enabled_channels}", flush=True)
        print(f"readback.tx_cyclic_buffer={sdr.tx_cyclic_buffer}", flush=True)
        print(f"readback.tx_buffer_present={sdr._txbuf is not None}", flush=True)
        print(f"readback.tx_buffer_identity={id(sdr._txbuf)}", flush=True)

    def off(self) -> None:
        if self.reuse_buffer and self.source._sdr is not None:
            self.source._sdr._set_iio_attr_float(
                f"voltage{self.source.radio_config.tx_channel}",
                "hardwaregain",
                True,
                E310CpiSource._TX_MUTE_GAIN_DB,
            )
            print("readback.tx_muted=true buffer_retained=true", flush=True)
        else:
            self.source.close()

    def close(self) -> None:
        self.source.close()

    @property
    def sdr(self):
        return self.source._sdr


class DdsTransmitter:
    def __init__(self, tx_channel: int, tx_port: str) -> None:
        try:
            import adi
        except ImportError as error:
            raise RuntimeError("pyadi-iio is required for E310 diagnostics") from error
        self.sdr = adi.ad9361(uri=URI)
        self.sdr.sample_rate = 30_000_000
        self.sdr.tx_rf_bandwidth = 20_000_000
        self.sdr.tx_lo = LO_HZ
        self.tx_channel = tx_channel
        self.sdr.tx_enabled_channels = [tx_channel]
        channel_name = f"voltage{tx_channel}"
        self.sdr._set_iio_attr(channel_name, "rf_port_select", True, tx_port)
        self.sdr._set_iio_attr_float(
            channel_name, "hardwaregain", True, TX_GAIN_DB
        )

    def on(self) -> None:
        self.sdr.dds_single_tone(
            DDS_OFFSET_HZ, TX_AMPLITUDE, channel=self.tx_channel
        )
        print(
            f"readback.tx_enabled_channels={self.sdr.tx_enabled_channels}",
            flush=True,
        )
        print(f"readback.dds_enabled={list(self.sdr.dds_enabled)}", flush=True)
        print(f"readback.dds_frequencies={list(self.sdr.dds_frequencies)}", flush=True)

    def off(self) -> None:
        self.sdr.disable_dds()

    def close(self) -> None:
        self.off()


def _import_adi():
    try:
        import adi
    except ImportError as error:
        raise RuntimeError("pyadi-iio is required for E310 diagnostics") from error
    return adi


def _read_or_none(reader):
    try:
        return reader()
    except Exception:
        return None


def snapshot_tx_state(
    sdr: object | None,
    *,
    phase: str,
    started_at: float,
    tx_channel: int,
) -> dict[str, object]:
    if sdr is None:
        return {
            "event": "tx_state",
            "elapsed_s": round(time.monotonic() - started_at, 3),
            "phase": phase,
            "tx_enabled_channels": None,
            "tx_cyclic_buffer": None,
            "tx_buffer_present": None,
            "dds_enabled": None,
            "hardware_gain_db": None,
        }

    tx_enabled_channels = _read_or_none(lambda: list(sdr.tx_enabled_channels))
    dds_enabled = _read_or_none(
        lambda: [bool(value) for value in sdr.dds_enabled]
    )
    hardware_gain = _read_or_none(
        lambda: float(
            sdr._get_iio_attr(
                f"voltage{tx_channel}", "hardwaregain", True
            )
        )
    )
    return {
        "event": "tx_state",
        "elapsed_s": round(time.monotonic() - started_at, 3),
        "phase": phase,
        "tx_enabled_channels": tx_enabled_channels,
        "tx_cyclic_buffer": _read_or_none(lambda: bool(sdr.tx_cyclic_buffer)),
        "tx_buffer_present": getattr(sdr, "_txbuf", None) is not None,
        "dds_enabled": dds_enabled,
        "hardware_gain_db": hardware_gain,
    }


def emit_boundary(*, phase: str, boundary: str, started_at: float) -> None:
    print(
        json.dumps(
            {
                "event": "phase_boundary",
                "elapsed_s": round(time.monotonic() - started_at, 3),
                "phase": phase,
                "boundary": boundary,
            },
            sort_keys=True,
        ),
        flush=True,
    )


class FmcwLifecycleTransmitter:
    """Expose TX/RX setup boundaries without using E310CpiSource.open()."""

    def __init__(
        self,
        tx_channel: int,
        tx_port: str,
        phase: str,
        reuse_buffer: bool,
        pre_upload_s: float,
    ) -> None:
        self.phase = phase
        self.reuse_buffer = reuse_buffer
        self.config = RadarConfig()
        self.radio_config = E310RadioConfig(
            uri=URI,
            lo_hz=LO_HZ,
            tx_channel=tx_channel,
            tx_port=tx_port,
            tx_gain_db=TX_GAIN_DB,
            tx_amplitude=TX_AMPLITUDE,
            pre_tx_settle_s=pre_upload_s,
            rx_gain_db=50.0,
        )
        self.source = E310CpiSource(self.config, self.radio_config)
        self.sdr = None
        self.started_at = time.monotonic()

    def _configure_tx(self) -> None:
        radio = self.radio_config
        emit_boundary(
            phase=self.phase,
            boundary="tx_configure_begin",
            started_at=self.started_at,
        )
        sdr = _import_adi().ad9361(uri=radio.uri)
        self.sdr = sdr
        self.source._sdr = sdr
        sdr.sample_rate = int(self.config.sample_rate_hz)
        sdr.tx_rf_bandwidth = int(self.config.bandwidth_hz)
        sdr.tx_lo = radio.lo_hz
        sdr.tx_enabled_channels = [radio.tx_channel]
        sdr._set_iio_attr(
            f"voltage{radio.tx_channel}",
            "rf_port_select",
            True,
            radio.tx_port,
        )
        sdr._set_iio_attr_float(
            f"voltage{radio.tx_channel}",
            "hardwaregain",
            True,
            radio.tx_gain_db,
        )
        emit_boundary(
            phase=self.phase,
            boundary="tx_configure_end",
            started_at=self.started_at,
        )

    def _upload_tx(self) -> None:
        emit_boundary(
            phase=self.phase,
            boundary="tx_upload_begin",
            started_at=self.started_at,
        )
        if self.radio_config.pre_tx_settle_s:
            time.sleep(self.radio_config.pre_tx_settle_s)
        self.sdr.tx_cyclic_buffer = True
        self.sdr.tx(self.source._tx_iq)
        self.source._tx_uploaded = True
        emit_boundary(
            phase=self.phase,
            boundary="tx_upload_end",
            started_at=self.started_at,
        )

    def _configure_rx(self) -> None:
        radio = self.radio_config
        emit_boundary(
            phase=self.phase,
            boundary="rx_configure_begin",
            started_at=self.started_at,
        )
        self.sdr.rx_rf_bandwidth = int(self.config.bandwidth_hz)
        self.sdr.rx_lo = radio.lo_hz
        self.sdr.rx_enabled_channels = [radio.rx_channel]
        self.sdr.rx_buffer_size = (
            self.config.cpi_samples
            + radio.sync_margin_samples(self.config)
        )
        self.sdr._set_iio_attr(
            f"voltage{radio.rx_channel}",
            "rf_port_select",
            False,
            radio.rx_port,
        )
        self.sdr._set_iio_attr(
            f"voltage{radio.rx_channel}",
            "gain_control_mode",
            False,
            "manual",
        )
        self.sdr._set_iio_attr_float(
            f"voltage{radio.rx_channel}",
            "hardwaregain",
            False,
            radio.rx_gain_db,
        )
        emit_boundary(
            phase=self.phase,
            boundary="rx_configure_end",
            started_at=self.started_at,
        )

    def on(self) -> None:
        if self.sdr is not None:
            self.sdr._set_iio_attr_float(
                f"voltage{self.radio_config.tx_channel}",
                "hardwaregain",
                True,
                TX_GAIN_DB,
            )
            emit_boundary(
                phase=self.phase,
                boundary="tx_unmuted",
                started_at=self.started_at,
            )
            return

        self.started_at = time.monotonic()
        self._configure_tx()
        self._upload_tx()
        if self.phase in {"fmcw-rx-configured", "fmcw-rx-read"}:
            self._configure_rx()
        if self.phase == "fmcw-rx-read":
            emit_boundary(
                phase=self.phase,
                boundary="rx_read_begin",
                started_at=self.started_at,
            )
            self.sdr.rx()
            emit_boundary(
                phase=self.phase,
                boundary="rx_read_end",
                started_at=self.started_at,
            )

    def off(self) -> None:
        if self.reuse_buffer and self.sdr is not None:
            self.source._mute_tx(self.sdr)
            emit_boundary(
                phase=self.phase,
                boundary="tx_muted",
                started_at=self.started_at,
            )
        else:
            self.close()

    def close(self) -> None:
        self.source.close()
        self.sdr = None


def hold_with_state(
    transmitter: object,
    *,
    phase: str,
    duration_s: float,
    sample_state_s: float,
    tx_channel: int,
) -> None:
    started_at = getattr(transmitter, "started_at", time.monotonic())
    deadline = time.monotonic() + duration_s
    while True:
        print(
            json.dumps(
                snapshot_tx_state(
                    transmitter.sdr,
                    phase=phase,
                    started_at=started_at,
                    tx_channel=tx_channel,
                ),
                sort_keys=True,
            ),
            flush=True,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        time.sleep(min(sample_state_s, remaining))


def print_plan(args: argparse.Namespace) -> None:
    if args.mode is not None:
        print(f"mode={args.mode}")
    print(f"phase={args.phase}")
    print(f"uri={URI}")
    print(f"tx_lo_hz={LO_HZ}")
    print(f"tx_gain_db={TX_GAIN_DB}")
    print(f"tx_amplitude={TX_AMPLITUDE}")
    print(f"tx_channel={args.tx_channel}")
    print(f"tx_port={args.tx_port}")
    print(f"reuse_buffer={'true' if args.reuse_buffer else 'false'}")
    print(f"pre_upload_s={args.pre_upload_s}")
    if args.mode == "dds":
        print(f"dds_output_hz={LO_HZ + DDS_OFFSET_HZ}")
    print(f"cycles={args.cycles}")
    print(f"on_s={args.on_s}")
    print(f"off_s={args.off_s}")
    print(f"sample_state_s={args.sample_state_s}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    lifecycle_phase = args.phase
    selected_mode = args.mode or lifecycle_phase.split("-", 1)[0]
    if args.reuse_buffer is None:
        args.reuse_buffer = selected_mode == "fmcw"
    if args.reuse_buffer and selected_mode != "fmcw":
        parser.error("--reuse-buffer is supported only with --mode fmcw")
    print_plan(args)
    if args.dry_run:
        print("hardware_access=false")
        return 0

    if lifecycle_phase is not None and lifecycle_phase != "dds-tx-only":
        transmitter = FmcwLifecycleTransmitter(
            args.tx_channel,
            args.tx_port,
            lifecycle_phase,
            args.reuse_buffer,
            args.pre_upload_s,
        )
    elif selected_mode == "fmcw":
        transmitter = FmcwTransmitter(
            args.tx_channel,
            args.tx_port,
            args.reuse_buffer,
            args.pre_upload_s,
        )
    else:
        transmitter = DdsTransmitter(args.tx_channel, args.tx_port)
    reported_phase = lifecycle_phase or f"legacy-{selected_mode}"
    interrupted = False
    try:
        for cycle in range(1, args.cycles + 1):
            print(f"cycle={cycle}/{args.cycles} tx=ON", flush=True)
            started_at = time.monotonic()
            emit_boundary(
                phase=reported_phase,
                boundary="tx_on_begin",
                started_at=started_at,
            )
            transmitter.on()
            emit_boundary(
                phase=reported_phase,
                boundary="tx_on_end",
                started_at=started_at,
            )
            hold_with_state(
                transmitter,
                phase=reported_phase,
                duration_s=args.on_s,
                sample_state_s=args.sample_state_s,
                tx_channel=args.tx_channel,
            )
            transmitter.off()
            print(f"cycle={cycle}/{args.cycles} tx=OFF", flush=True)
            time.sleep(args.off_s)
    except KeyboardInterrupt:
        interrupted = True
        print("diagnostic_interrupted=true", flush=True)
    finally:
        transmitter.close()
        print("diagnostic_complete=true tx=OFF", flush=True)
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
