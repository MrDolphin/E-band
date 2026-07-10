from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback import ModemConfig, QpskLoopbackModem


def print_rx_stats(rx: np.ndarray) -> None:
    magnitude = np.abs(rx)
    rms = float(np.sqrt(np.mean(magnitude * magnitude)))
    peak = float(np.max(magnitude))
    mean_i = float(np.mean(np.real(rx)))
    mean_q = float(np.mean(np.imag(rx)))
    clip_ratio = float(np.mean(magnitude > 0.90))
    print(f"rx_samples={len(rx)}")
    print(f"rx_rms={rms:.6f}")
    print(f"rx_peak={peak:.6f}")
    print(f"rx_dc_i={mean_i:.6f}")
    print(f"rx_dc_q={mean_q:.6f}")
    print(f"rx_clip_ratio={clip_ratio:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transmit and receive one QPSK packet through E310 RF loopback.")
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--message", default="hello rf")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--sample-rate", type=int, default=1_000_000)
    parser.add_argument("--symbol-rate", type=int, default=250_000)
    parser.add_argument("--bandwidth", type=int, default=800_000)
    parser.add_argument("--tx-gain-db", type=float, default=-40.0, help="AD9361 TX hardware gain/attenuation value.")
    parser.add_argument("--rx-gain-db", type=float, default=20.0)
    parser.add_argument("--rx-buffer", type=int, default=32768)
    parser.add_argument("--stats-only", action="store_true", help="Capture RX samples and print level stats without decoding.")
    args = parser.parse_args()

    try:
        import adi
    except ImportError:
        print("pyadi-iio is not installed. Install with: python -m pip install pyadi-iio", file=sys.stderr)
        return 2

    modem = QpskLoopbackModem(ModemConfig(sample_rate=args.sample_rate, symbol_rate=args.symbol_rate))
    tx = modem.transmit(args.message.encode("utf-8"), sequence=1)
    tx_padded = np.concatenate([tx, np.zeros(args.rx_buffer, dtype=np.complex64)])

    sdr = adi.ad9361(uri=args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_rf_bandwidth = int(args.bandwidth)
    sdr.tx_rf_bandwidth = int(args.bandwidth)
    sdr.rx_lo = int(args.lo_hz)
    sdr.tx_lo = int(args.lo_hz)
    sdr.rx_enabled_channels = [0]
    sdr.tx_enabled_channels = [0]
    sdr.rx_buffer_size = int(args.rx_buffer)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = float(args.rx_gain_db)
    sdr.tx_hardwaregain_chan0 = float(args.tx_gain_db)

    if hasattr(sdr, "tx_destroy_buffer"):
        sdr.tx_destroy_buffer()

    # Send a cyclic burst long enough for the next RX buffer to contain the frame.
    sdr.tx_cyclic_buffer = True
    sdr.tx(tx_padded)
    time.sleep(0.1)
    raw = sdr.rx()

    if hasattr(sdr, "tx_destroy_buffer"):
        sdr.tx_destroy_buffer()

    rx = np.asarray(raw[0] if isinstance(raw, list) else raw, dtype=np.complex64)
    print_rx_stats(rx)
    if args.stats_only:
        return 0

    packet = modem.receive(rx)
    print(f"sequence={packet.sequence}")
    print(f"payload={packet.payload.decode('utf-8', errors='replace')}")
    print("crc_ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
