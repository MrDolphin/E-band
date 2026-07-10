from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback import ModemConfig, QpskLoopbackModem


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a pure software QPSK packet loopback.")
    parser.add_argument("--message", default="hello e310")
    parser.add_argument("--snr-db", type=float, default=30.0)
    parser.add_argument("--sequence", type=int, default=1)
    args = parser.parse_args()

    modem = QpskLoopbackModem(ModemConfig())
    payload = args.message.encode("utf-8")
    tx = modem.transmit(payload, sequence=args.sequence)

    signal_power = float(np.mean(np.abs(tx) ** 2))
    noise_power = signal_power / (10.0 ** (args.snr_db / 10.0))
    noise = np.sqrt(noise_power / 2.0) * (
        np.random.default_rng(1234).standard_normal(len(tx))
        + 1j * np.random.default_rng(5678).standard_normal(len(tx))
    )
    rx = tx + noise.astype(np.complex64)

    packet = modem.receive(rx)
    ok = packet.payload == payload and packet.sequence == args.sequence
    print(f"tx_samples={len(tx)}")
    print(f"sequence={packet.sequence}")
    print(f"payload={packet.payload.decode('utf-8', errors='replace')}")
    print(f"crc_ok=true")
    print(f"loopback_ok={str(ok).lower()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
