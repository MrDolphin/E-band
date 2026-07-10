from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def parse_key_values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E310 RF loopback payload tests in small TX batches.")
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--packets-per-batch", type=int, default=20)
    parser.add_argument("--artifact-prefix", type=Path, default=Path("artifacts/payload_batch_15mbps"))
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--sample-rate", type=int, default=30_000_000)
    parser.add_argument("--symbol-rate", type=int, default=7_500_000)
    parser.add_argument("--bandwidth", type=int, default=20_000_000)
    parser.add_argument("--tx-gain-db", type=float, default=-12.0)
    parser.add_argument("--tx-amplitude", type=float, default=0.5)
    parser.add_argument("--tx-dac-scale", type=float, default=8192.0)
    parser.add_argument("--rx-gain-db", type=float, default=10.0)
    parser.add_argument("--tx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--rx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--tx-port", default="B")
    parser.add_argument("--rx-port", default="B_BALANCED")
    parser.add_argument("--payload-pattern", choices=("counter", "random"), default="counter")
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--tx-settle-sec", type=float, default=0.5)
    parser.add_argument("--rx-discard-buffers", type=int, default=1)
    parser.add_argument("--tx-cyclic-copies", type=int, default=2)
    parser.add_argument("--retries", type=int, default=2, help="Retries per failed batch.")
    parser.add_argument("--retry-delay-sec", type=float, default=0.5)
    args = parser.parse_args()

    if args.batches <= 0:
        print("--batches must be positive", file=sys.stderr)
        return 2
    if args.packets_per_batch <= 0:
        print("--packets-per-batch must be positive", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("--retries must be non-negative", file=sys.stderr)
        return 2
    if args.retry_delay_sec < 0.0:
        print("--retry-delay-sec must be non-negative", file=sys.stderr)
        return 2

    script = Path(__file__).with_name("e310_rf_loopback.py")
    total_expected = args.batches * args.packets_per_batch
    total_ok = 0
    first_payload_bitrate_est = 0.0

    for batch_index in range(1, args.batches + 1):
        batch_ok = 0
        batch_succeeded = False
        for attempt in range(1, args.retries + 2):
            batch_prefix = args.artifact_prefix.with_name(
                f"{args.artifact_prefix.name}_batch{batch_index:03d}_try{attempt:02d}"
            )
            command = [
                sys.executable,
                str(script),
                "--uri",
                args.uri,
                "--sample-rate",
                str(args.sample_rate),
                "--symbol-rate",
                str(args.symbol_rate),
                "--bandwidth",
                str(args.bandwidth),
                "--tx-gain-db",
                str(args.tx_gain_db),
                "--tx-amplitude",
                str(args.tx_amplitude),
                "--tx-dac-scale",
                str(args.tx_dac_scale),
                "--rx-gain-db",
                str(args.rx_gain_db),
                "--tx-channel",
                str(args.tx_channel),
                "--rx-channel",
                str(args.rx_channel),
                "--tx-port",
                args.tx_port,
                "--rx-port",
                args.rx_port,
                "--packet-count",
                str(args.packets_per_batch),
                "--payload-pattern",
                args.payload_pattern,
                "--payload-bytes",
                str(args.payload_bytes),
                "--tx-settle-sec",
                str(args.tx_settle_sec),
                "--rx-discard-buffers",
                str(args.rx_discard_buffers),
                "--tx-cyclic-copies",
                str(args.tx_cyclic_copies),
                "--save-iq",
                str(batch_prefix.with_suffix(".npz")),
                "--plot-prefix",
                str(batch_prefix),
            ]
            print(f"batch={batch_index}")
            print(f"batch_attempt={attempt}")
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(result.stdout, end="")
            values = parse_key_values(result.stdout)
            batch_ok = int(values.get("packets_ok", "0"))
            first_payload_bitrate_est = first_payload_bitrate_est or float(values.get("payload_bitrate_est_bps", "0"))
            print(f"batch_packets_ok={batch_ok}")
            if result.returncode == 0 and batch_ok == args.packets_per_batch:
                batch_succeeded = True
                break
            if attempt <= args.retries:
                print(f"batch_retrying={batch_index}")
                time.sleep(args.retry_delay_sec)

        total_ok += batch_ok
        if not batch_succeeded:
            print(f"batch_failed={batch_index}")
            break

    total_error_rate = 1.0 - total_ok / max(total_expected, 1)
    print(f"total_batches={args.batches}")
    print(f"total_packets_expected={total_expected}")
    print(f"total_packets_ok={total_ok}")
    print(f"total_packet_error_rate={total_error_rate:.6f}")
    print(f"payload_bitrate_est_bps={first_payload_bitrate_est:.0f}")
    print(f"payload_bitrate_ok_bps={first_payload_bitrate_est * total_ok / max(total_expected, 1):.0f}")
    return 0 if total_ok == total_expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
