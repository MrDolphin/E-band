from __future__ import annotations

import argparse
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
        description="Run visible E310 TX on/off diagnostics for FMCW DMA or DDS."
    )
    parser.add_argument("--mode", choices=("fmcw", "dds"), required=True)
    parser.add_argument("--cycles", type=positive_cycles, default=3)
    parser.add_argument("--on-s", type=positive_float, default=3.0)
    parser.add_argument("--off-s", type=positive_float, default=3.0)
    parser.add_argument("--tx-channel", type=int, choices=(0, 1), default=0)
    parser.add_argument("--tx-port", choices=("A", "B"), default="B")
    parser.add_argument("--pre-upload-s", type=nonnegative_wait, default=1.0)
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


def print_plan(args: argparse.Namespace) -> None:
    print(f"mode={args.mode}")
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.reuse_buffer is None:
        args.reuse_buffer = args.mode == "fmcw"
    if args.reuse_buffer and args.mode != "fmcw":
        parser.error("--reuse-buffer is supported only with --mode fmcw")
    print_plan(args)
    if args.dry_run:
        print("hardware_access=false")
        return 0

    transmitter = (
        FmcwTransmitter(
            args.tx_channel,
            args.tx_port,
            args.reuse_buffer,
            args.pre_upload_s,
        )
        if args.mode == "fmcw"
        else DdsTransmitter(args.tx_channel, args.tx_port)
    )
    interrupted = False
    try:
        for cycle in range(1, args.cycles + 1):
            print(f"cycle={cycle}/{args.cycles} tx=ON", flush=True)
            transmitter.on()
            time.sleep(args.on_s)
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
