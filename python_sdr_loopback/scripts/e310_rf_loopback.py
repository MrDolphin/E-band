from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback import ModemConfig, QpskLoopbackModem


def destroy_iio_buffers(sdr) -> None:
    for method_name in ("tx_destroy_buffer", "rx_destroy_buffer"):
        method = getattr(sdr, method_name, None)
        if method is None:
            continue
        try:
            method()
        except Exception:
            pass


def dbfs(value: float, full_scale: float) -> float:
    return 20.0 * np.log10(max(value, 1e-12) / full_scale)


def print_tx_stats(tx: np.ndarray) -> None:
    magnitude = np.abs(tx)
    print(f"tx_samples={len(tx)}")
    print(f"tx_rms_counts={float(np.sqrt(np.mean(magnitude * magnitude))):.3f}")
    print(f"tx_peak_counts={float(np.max(magnitude)):.3f}")


def print_rx_stats(rx: np.ndarray, adc_full_scale: float) -> None:
    magnitude = np.abs(rx)
    rms = float(np.sqrt(np.mean(magnitude * magnitude)))
    peak = float(np.max(magnitude))
    mean_i = float(np.mean(np.real(rx)))
    mean_q = float(np.mean(np.imag(rx)))
    clip_ratio = float(np.mean(magnitude > 0.95 * adc_full_scale))
    print(f"rx_samples={len(rx)}")
    print(f"rx_rms_counts={rms:.3f}")
    print(f"rx_peak_counts={peak:.3f}")
    print(f"rx_rms_dbfs={dbfs(rms, adc_full_scale):.2f}")
    print(f"rx_peak_dbfs={dbfs(peak, adc_full_scale):.2f}")
    print(f"rx_dc_i={mean_i:.6f}")
    print(f"rx_dc_q={mean_q:.6f}")
    print(f"rx_clip_ratio={clip_ratio:.6f}")


def set_if_present(obj, name: str, value) -> bool:
    if not hasattr(obj, name):
        return False
    setattr(obj, name, value)
    return True


def get_if_present(obj, name: str):
    if not hasattr(obj, name):
        return "<unsupported>"
    return getattr(obj, name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transmit and receive one QPSK packet through E310 RF loopback.")
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--message", default="hello rf")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--sample-rate", type=int, default=1_000_000)
    parser.add_argument("--symbol-rate", type=int, default=250_000)
    parser.add_argument("--bandwidth", type=int, default=800_000)
    parser.add_argument("--tx-gain-db", type=float, default=-40.0, help="AD9361 TX hardware gain/attenuation value.")
    parser.add_argument("--tx-amplitude", type=float, default=0.25, help="Digital baseband amplitude before AD9361 TX.")
    parser.add_argument("--tx-dac-scale", type=float, default=8192.0, help="Scale normalized modem IQ to DAC sample counts.")
    parser.add_argument("--adc-full-scale", type=float, default=2048.0, help="ADC full-scale count used for RX dBFS and clipping stats.")
    parser.add_argument("--rx-gain-db", type=float, default=20.0)
    parser.add_argument("--rx-buffer", type=int, default=32768)
    parser.add_argument("--tx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--rx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--tx-port", default="A", help="AD9361 TX RF port, commonly A or B.")
    parser.add_argument("--rx-port", default="A_BALANCED", help="AD9361 RX RF port, commonly A_BALANCED or B_BALANCED.")
    parser.add_argument("--stats-only", action="store_true", help="Capture RX samples and print level stats without decoding.")
    args = parser.parse_args()

    try:
        import adi
    except ImportError:
        print("pyadi-iio is not installed. Install with: python -m pip install pyadi-iio", file=sys.stderr)
        return 2

    if not 0.0 < args.tx_amplitude <= 1.0:
        print("--tx-amplitude must be in (0, 1]", file=sys.stderr)
        return 2
    if args.tx_dac_scale <= 0.0:
        print("--tx-dac-scale must be positive", file=sys.stderr)
        return 2
    if args.adc_full_scale <= 0.0:
        print("--adc-full-scale must be positive", file=sys.stderr)
        return 2

    modem = QpskLoopbackModem(
        ModemConfig(
            sample_rate=args.sample_rate,
            symbol_rate=args.symbol_rate,
            tx_amplitude=args.tx_amplitude,
        )
    )
    tx = modem.transmit(args.message.encode("utf-8"), sequence=1)
    tx *= float(args.tx_dac_scale)
    repeat_count = max(2, int(np.ceil(args.rx_buffer / len(tx))) + 1)
    tx_padded = np.tile(tx, repeat_count).astype(np.complex64)
    print_tx_stats(tx)
    print(f"tx_cyclic_samples={len(tx_padded)}")

    sdr = adi.ad9361(uri=args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_rf_bandwidth = int(args.bandwidth)
    sdr.tx_rf_bandwidth = int(args.bandwidth)
    sdr.rx_lo = int(args.lo_hz)
    sdr.tx_lo = int(args.lo_hz)
    sdr.rx_enabled_channels = [args.rx_channel]
    sdr.tx_enabled_channels = [args.tx_channel]
    sdr.rx_buffer_size = int(args.rx_buffer)
    set_if_present(sdr, "rx_rf_port_select", args.rx_port)
    set_if_present(sdr, "tx_rf_port_select", args.tx_port)
    set_if_present(sdr, f"gain_control_mode_chan{args.rx_channel}", "manual")
    set_if_present(sdr, f"rx_hardwaregain_chan{args.rx_channel}", float(args.rx_gain_db))
    set_if_present(sdr, f"tx_hardwaregain_chan{args.tx_channel}", float(args.tx_gain_db))
    print(f"tx_channel={args.tx_channel}")
    print(f"rx_channel={args.rx_channel}")
    print(f"tx_rf_port_select={get_if_present(sdr, 'tx_rf_port_select')}")
    print(f"rx_rf_port_select={get_if_present(sdr, 'rx_rf_port_select')}")
    print(f"tx_hardwaregain_chan{args.tx_channel}={get_if_present(sdr, f'tx_hardwaregain_chan{args.tx_channel}')}")
    print(f"rx_hardwaregain_chan{args.rx_channel}={get_if_present(sdr, f'rx_hardwaregain_chan{args.rx_channel}')}")

    destroy_iio_buffers(sdr)

    try:
        # Send a cyclic burst long enough for the next RX buffer to contain the frame.
        sdr.tx_cyclic_buffer = True
        sdr.tx(tx_padded)
        time.sleep(0.1)
        raw = sdr.rx()
    except OSError as exc:
        print(f"iio_error={exc}", file=sys.stderr)
        print(
            "IIO RX buffer is busy. Close GNU Radio/IIO tools/other Python runs, "
            "then power-cycle or reboot the E310 if the busy state persists.",
            file=sys.stderr,
        )
        return 3
    finally:
        destroy_iio_buffers(sdr)

    rx = np.asarray(raw[0] if isinstance(raw, list) else raw, dtype=np.complex64)
    print_rx_stats(rx, float(args.adc_full_scale))
    if args.stats_only:
        return 0

    packet = modem.receive(rx)
    print(f"sequence={packet.sequence}")
    print(f"payload={packet.payload.decode('utf-8', errors='replace')}")
    print("crc_ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
