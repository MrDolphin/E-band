from __future__ import annotations

import argparse
import subprocess
import sys
import time
import zlib
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
    parser = argparse.ArgumentParser(description="Transfer a file over E310 RF loopback in bounded-size batches.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--batch-bytes", type=int, default=200_000)
    parser.add_argument("--artifact-prefix", type=Path, default=Path("artifacts/file_batch"))
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
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--file-guard-packets", type=int, default=0)
    parser.add_argument("--tx-cyclic-copies", type=int, default=3)
    parser.add_argument("--rx-discard-buffers", type=int, default=0)
    parser.add_argument("--min-rx-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--rx-level-retries", type=int, default=8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--save-batch-iq", action="store_true")
    parser.add_argument("--plot-batches", action="store_true")
    args = parser.parse_args()

    if args.batch_bytes <= 0:
        print("--batch-bytes must be positive", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("--retries must be non-negative", file=sys.stderr)
        return 2
    if args.file_guard_packets < 0:
        print("--file-guard-packets must be non-negative", file=sys.stderr)
        return 2

    data = args.input_file.read_bytes()
    total_batches = max(1, (len(data) + args.batch_bytes - 1) // args.batch_bytes)
    input_crc = zlib.crc32(data) & 0xFFFFFFFF
    script = Path(__file__).with_name("e310_rf_loopback.py")
    args.artifact_prefix.parent.mkdir(parents=True, exist_ok=True)

    recovered_parts: list[bytes] = []
    first_payload_bitrate_est = 0.0
    transfer_start = time.perf_counter()
    for batch_index in range(total_batches):
        batch_start = time.perf_counter()
        start = batch_index * args.batch_bytes
        part = data[start : start + args.batch_bytes]
        part_input = args.artifact_prefix.with_name(f"{args.artifact_prefix.name}_part{batch_index + 1:04d}_input.bin")
        part_output = args.artifact_prefix.with_name(f"{args.artifact_prefix.name}_part{batch_index + 1:04d}_output.bin")
        part_input.write_bytes(part)

        batch_ok = False
        for attempt in range(1, args.retries + 2):
            attempt_prefix = args.artifact_prefix.with_name(
                f"{args.artifact_prefix.name}_part{batch_index + 1:04d}_try{attempt:02d}"
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
                "--input-file",
                str(part_input),
                "--output-file",
                str(part_output),
                "--payload-bytes",
                str(args.payload_bytes),
                "--file-guard-packets",
                str(args.file_guard_packets),
                "--tx-cyclic-copies",
                str(args.tx_cyclic_copies),
                "--rx-discard-buffers",
                str(args.rx_discard_buffers),
                "--min-rx-rms-dbfs",
                str(args.min_rx_rms_dbfs),
                "--rx-level-retries",
                str(args.rx_level_retries),
            ]
            if args.save_batch_iq:
                command.extend(["--save-iq", str(attempt_prefix.with_suffix(".npz"))])
            if args.plot_batches:
                command.extend(["--plot-prefix", str(attempt_prefix)])

            print(f"file_batch={batch_index + 1}")
            print(f"file_batches_total={total_batches}")
            print(f"batch_attempt={attempt}")
            print(f"batch_bytes={len(part)}")
            attempt_start = time.perf_counter()
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            attempt_elapsed = time.perf_counter() - attempt_start
            print(result.stdout, end="")
            print(f"attempt_elapsed_sec={attempt_elapsed:.3f}")
            values = parse_key_values(result.stdout)
            first_payload_bitrate_est = first_payload_bitrate_est or float(values.get("payload_bitrate_est_bps", "0"))
            if result.returncode == 0 and values.get("file_ok") == "true" and part_output.exists():
                recovered_parts.append(part_output.read_bytes())
                batch_ok = True
                print(f"file_batch_ok={batch_index + 1}")
                print(f"batch_elapsed_sec={time.perf_counter() - batch_start:.3f}")
                break
            if attempt <= args.retries:
                print(f"file_batch_retrying={batch_index + 1}")

        if not batch_ok:
            print(f"file_batch_failed={batch_index + 1}")
            break

    recovered = b"".join(recovered_parts)
    total_elapsed = time.perf_counter() - transfer_start
    output_crc = zlib.crc32(recovered) & 0xFFFFFFFF
    file_ok = recovered == data
    if file_ok:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_bytes(recovered)

    print(f"input_file={args.input_file}")
    print(f"output_file={args.output_file if file_ok else ''}")
    print(f"input_bytes={len(data)}")
    print(f"output_bytes={len(recovered)}")
    print(f"input_crc32={input_crc}")
    print(f"output_crc32={output_crc}")
    print(f"file_batches_total={total_batches}")
    print(f"file_batches_ok={len(recovered_parts)}")
    print(f"payload_bitrate_est_bps={first_payload_bitrate_est:.0f}")
    print(f"total_elapsed_sec={total_elapsed:.3f}")
    print(f"file_goodput_bps={len(recovered) * 8.0 / max(total_elapsed, 1e-9):.0f}")
    print(f"file_ok={str(file_ok).lower()}")
    return 0 if file_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
