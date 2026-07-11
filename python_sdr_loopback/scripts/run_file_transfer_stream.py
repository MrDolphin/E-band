from __future__ import annotations

import argparse
import sys
import time
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from e310_rf_loopback import (  # noqa: E402
    capture_once,
    configure_sdr,
    dbfs,
    destroy_iio_buffers,
    get_channel_attr,
    print_rx_stats,
)
from sdr_loopback import ModemConfig, QpskLoopbackModem  # noqa: E402
from sdr_loopback.file_payload import (  # noqa: E402
    FILE_HEADER_SIZE,
    build_file_payloads,
    dewhiten_payload,
    parse_file_payload,
    recover_file,
    whiten_payload,
)
from sdr_loopback.packet import Packet  # noqa: E402
from sdr_loopback.payload import payload_efficiency, raw_bitrate_bps  # noqa: E402


def rx_level_dbfs(rx: np.ndarray, full_scale: float) -> float:
    magnitude = np.abs(rx)
    rms = float(np.sqrt(np.mean(magnitude * magnitude)))
    return dbfs(rms, full_scale)


def max_rx_buffer_size(modem: QpskLoopbackModem, args, max_part_bytes: int) -> tuple[int, float, int]:
    chunk_capacity = int(args.payload_bytes) - FILE_HEADER_SIZE
    if chunk_capacity <= 0:
        raise ValueError(f"--payload-bytes must be greater than {FILE_HEADER_SIZE}")
    max_file_chunks = max(1, (max_part_bytes + chunk_capacity - 1) // chunk_capacity)
    sample_payload = bytes(int(args.payload_bytes))
    samples_per_packet = float(len(modem.transmit(sample_payload, sequence=1)))
    max_packets = max_file_chunks + int(args.file_guard_packets)
    minimum = int(np.ceil(max_packets * samples_per_packet * float(args.rx_frame_copies) + samples_per_packet * 2))
    return max(int(args.rx_buffer), minimum), samples_per_packet, max_file_chunks


def build_batch_payloads(part: bytes, args) -> tuple[list[bytes], list[bytes]]:
    file_payloads = build_file_payloads(part, int(args.payload_bytes))
    wire_payloads = [whiten_payload(payload) for payload in file_payloads]
    guard_payloads = [bytes([0x55]) * int(args.payload_bytes) for _ in range(int(args.file_guard_packets))]
    payloads = guard_payloads + wire_payloads
    return file_payloads, payloads


def modulate_batch_tx(modem: QpskLoopbackModem, payloads: list[bytes], args) -> np.ndarray:
    tx = np.concatenate(
        [modem.transmit(payload, sequence=sequence) for sequence, payload in enumerate(payloads, start=1)]
    )
    tx *= float(args.tx_dac_scale)
    return np.tile(tx, int(args.tx_cyclic_copies)).astype(np.complex64)


def recover_part_from_packets(packets: list[Packet], file_payloads: list[bytes]) -> tuple[bytes, dict]:
    seen_chunks: set[int] = set()
    recovered_payloads: list[bytes] = []
    for packet in packets:
        dewhitened = dewhiten_payload(packet.payload)
        try:
            chunk = parse_file_payload(dewhitened)
        except ValueError:
            continue
        if 0 <= chunk.chunk_index < len(file_payloads) and dewhitened == file_payloads[chunk.chunk_index]:
            if chunk.chunk_index not in seen_chunks:
                seen_chunks.add(chunk.chunk_index)
                recovered_payloads.append(dewhitened)

    if not recovered_payloads:
        return b"", {
            "packets_decoded": len(packets),
            "chunks_ok": 0,
            "missing_chunks": list(range(len(file_payloads))),
            "file_ok": False,
        }

    recovered, report = recover_file(recovered_payloads)
    report = dict(report)
    report["packets_decoded"] = len(packets)
    report["file_ok"] = report.get("file_crc32") == report.get("file_crc32_actual")
    return recovered, report


def print_first_batch_sdr_info(sdr, args) -> None:
    print(f"tx_channel={args.tx_channel}")
    print(f"rx_channel={args.rx_channel}")
    print(f"tx_rf_port_select={get_channel_attr(sdr, args.tx_channel, 'rf_port_select', True)}")
    print(f"rx_rf_port_select={get_channel_attr(sdr, args.rx_channel, 'rf_port_select', False)}")
    print(f"tx_hardwaregain_chan{args.tx_channel}={sdr._get_iio_attr(f'voltage{args.tx_channel}', 'hardwaregain', True)}")
    print(f"rx_hardwaregain_chan{args.rx_channel}={sdr._get_iio_attr(f'voltage{args.rx_channel}', 'hardwaregain', False)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transfer a file over E310 RF loopback with one reusable SDR process.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--batch-bytes", type=int, default=120_000)
    parser.add_argument("--artifact-prefix", type=Path, default=Path("artifacts/file_stream"))
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--lo-hz", type=int, default=900_000_000)
    parser.add_argument("--sample-rate", type=int, default=30_000_000)
    parser.add_argument("--symbol-rate", type=int, default=7_500_000)
    parser.add_argument("--bandwidth", type=int, default=20_000_000)
    parser.add_argument("--tx-gain-db", type=float, default=-12.0)
    parser.add_argument("--tx-amplitude", type=float, default=0.5)
    parser.add_argument("--tx-dac-scale", type=float, default=8192.0)
    parser.add_argument("--adc-full-scale", type=float, default=2048.0)
    parser.add_argument("--rx-gain-db", type=float, default=10.0)
    parser.add_argument("--rx-buffer", type=int, default=32768)
    parser.add_argument("--tx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--rx-channel", type=int, default=0, choices=(0, 1))
    parser.add_argument("--tx-port", default="B")
    parser.add_argument("--rx-port", default="B_BALANCED")
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--file-guard-packets", type=int, default=0)
    parser.add_argument("--tx-settle-sec", type=float, default=0.25)
    parser.add_argument("--rx-discard-buffers", type=int, default=0)
    parser.add_argument("--tx-cyclic-copies", type=int, default=3)
    parser.add_argument(
        "--inter-batch-sec",
        type=float,
        default=0.25,
        help="Seconds to wait between successful batches so the IIO buffers settle.",
    )
    parser.add_argument(
        "--rx-frame-copies",
        type=float,
        default=1.0,
        help="RX buffer length in transmitted-frame copies, plus two packet margins.",
    )
    parser.add_argument("--min-rx-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--rx-level-retries", type=int, default=8)
    parser.add_argument(
        "--write-progressive",
        action="store_true",
        help="Append each recovered batch to --output-file immediately instead of only writing at the end.",
    )
    args = parser.parse_args()

    if args.batch_bytes <= 0:
        print("--batch-bytes must be positive", file=sys.stderr)
        return 2
    if args.payload_bytes <= FILE_HEADER_SIZE:
        print(f"--payload-bytes must be greater than {FILE_HEADER_SIZE}", file=sys.stderr)
        return 2
    if args.file_guard_packets < 0:
        print("--file-guard-packets must be non-negative", file=sys.stderr)
        return 2
    if args.rx_level_retries < 0:
        print("--rx-level-retries must be non-negative", file=sys.stderr)
        return 2
    if args.rx_frame_copies <= 0.0:
        print("--rx-frame-copies must be positive", file=sys.stderr)
        return 2
    if args.inter_batch_sec < 0.0:
        print("--inter-batch-sec must be non-negative", file=sys.stderr)
        return 2

    try:
        import adi
    except ImportError:
        print("pyadi-iio is not installed. Install with: python -m pip install pyadi-iio", file=sys.stderr)
        return 2

    transfer_start = time.perf_counter()
    data = args.input_file.read_bytes()
    input_crc = zlib.crc32(data) & 0xFFFFFFFF
    total_batches = max(1, (len(data) + args.batch_bytes - 1) // args.batch_bytes)
    modem = QpskLoopbackModem(
        ModemConfig(sample_rate=args.sample_rate, symbol_rate=args.symbol_rate, tx_amplitude=args.tx_amplitude)
    )
    max_part_bytes = min(len(data), int(args.batch_bytes))
    rx_buffer_size, samples_per_packet, _max_file_chunks = max_rx_buffer_size(modem, args, max_part_bytes)
    encoded_packet_size = len(Packet(sequence=1, payload=bytes(int(args.payload_bytes))).encode())
    bitrate_est = raw_bitrate_bps(float(args.symbol_rate)) * payload_efficiency(
        int(args.payload_bytes), encoded_packet_size, 8
    )

    print(f"input_file={args.input_file}")
    print(f"input_bytes={len(data)}")
    print(f"file_batches_total={total_batches}")
    print(f"batch_bytes={args.batch_bytes}")
    print(f"payload_bytes={args.payload_bytes}")
    print(f"payload_bitrate_est_bps={bitrate_est:.0f}")
    print(f"rx_buffer_requested={args.rx_buffer}")
    print(f"rx_buffer_used={rx_buffer_size}")
    print(f"rx_frame_copies={args.rx_frame_copies:.2f}")
    print(f"inter_batch_sec={args.inter_batch_sec:.3f}")
    print(f"samples_per_packet={samples_per_packet:.1f}")
    print("stream_context_reuse=true")
    print(f"write_progressive={str(bool(args.write_progressive)).lower()}")

    recovered_parts: list[bytes] = []
    progressive_bytes = 0
    capture_attempts_total = 0
    context_recreates = 0
    stage_totals = {
        "payload_build_elapsed_sec": 0.0,
        "modulate_elapsed_sec": 0.0,
        "sdr_capture_elapsed_sec": 0.0,
        "decode_elapsed_sec": 0.0,
        "file_recover_elapsed_sec": 0.0,
    }

    sdr = None
    output_handle = None
    try:
        if args.write_progressive:
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            output_handle = args.output_file.open("wb")
        context_start = time.perf_counter()
        sdr = configure_sdr(adi, args, rx_buffer_size)
        print(f"initial_sdr_context_elapsed_sec={time.perf_counter() - context_start:.3f}")
        print_first_batch_sdr_info(sdr, args)

        for batch_index in range(total_batches):
            batch_start = time.perf_counter()
            start = batch_index * args.batch_bytes
            part = data[start : start + args.batch_bytes]
            print(f"file_batch={batch_index + 1}")
            print(f"current_batch_bytes={len(part)}")

            payload_start = time.perf_counter()
            file_payloads, payloads = build_batch_payloads(part, args)
            payload_elapsed = time.perf_counter() - payload_start
            stage_totals["payload_build_elapsed_sec"] += payload_elapsed
            modulate_start = time.perf_counter()
            tx_padded = modulate_batch_tx(modem, payloads, args)
            modulate_elapsed = time.perf_counter() - modulate_start
            stage_totals["modulate_elapsed_sec"] += modulate_elapsed
            print(f"batch_file_chunks={len(file_payloads)}")
            print(f"batch_tx_cyclic_samples={len(tx_padded)}")
            print(f"batch_payload_build_elapsed_sec={payload_elapsed:.3f}")
            print(f"batch_modulate_elapsed_sec={modulate_elapsed:.3f}")

            rx = None
            level = -999.0
            capture_start = time.perf_counter()
            for attempt in range(1, int(args.rx_level_retries) + 2):
                capture_attempts_total += 1
                try:
                    rx = capture_once(sdr, tx_padded, float(args.tx_settle_sec), int(args.rx_discard_buffers))
                except OSError as exc:
                    print(f"iio_error={exc}")
                    if attempt > int(args.rx_level_retries):
                        raise
                    destroy_iio_buffers(sdr)
                    context_recreates += 1
                    sdr = configure_sdr(adi, args, rx_buffer_size)
                    print(f"stream_context_retry={context_recreates}")
                    time.sleep(0.25)
                    continue

                level = rx_level_dbfs(rx, float(args.adc_full_scale))
                print(f"capture_attempt={attempt}")
                print(f"rx_rms_dbfs_attempt={level:.2f}")
                if level >= float(args.min_rx_rms_dbfs):
                    break
                if attempt <= int(args.rx_level_retries):
                    destroy_iio_buffers(sdr)
                    context_recreates += 1
                    sdr = configure_sdr(adi, args, rx_buffer_size)
                    print(f"stream_context_retry={context_recreates}")
                    time.sleep(0.25)

            capture_elapsed = time.perf_counter() - capture_start
            stage_totals["sdr_capture_elapsed_sec"] += capture_elapsed
            if rx is None or level < float(args.min_rx_rms_dbfs):
                print(f"file_batch_failed={batch_index + 1}")
                break

            print_rx_stats(rx, float(args.adc_full_scale))
            decode_start = time.perf_counter()
            max_packets = (len(file_payloads) + int(args.file_guard_packets)) * max(2, int(np.ceil(args.rx_frame_copies)) + 1)
            packets = modem.receive_many(rx, max_packets)
            decode_elapsed = time.perf_counter() - decode_start
            recover_start = time.perf_counter()
            recovered, report = recover_part_from_packets(packets, file_payloads)
            recover_elapsed = time.perf_counter() - recover_start
            stage_totals["decode_elapsed_sec"] += decode_elapsed
            stage_totals["file_recover_elapsed_sec"] += recover_elapsed
            print(f"packets_decoded={report.get('packets_decoded', 0)}")
            print(f"chunks_ok={report.get('chunks_ok', 0)}")
            print(f"missing_chunks={report.get('missing_chunks', [])}")
            print(f"batch_file_ok={str(bool(report.get('file_ok'))).lower()}")
            print(f"batch_sdr_capture_elapsed_sec={capture_elapsed:.3f}")
            print(f"batch_decode_elapsed_sec={decode_elapsed:.3f}")
            print(f"batch_file_recover_elapsed_sec={recover_elapsed:.3f}")
            print(f"batch_elapsed_sec={time.perf_counter() - batch_start:.3f}")

            if not report.get("file_ok") or recovered != part:
                print(f"file_batch_failed={batch_index + 1}")
                break

            recovered_parts.append(recovered)
            progressive_bytes += len(recovered)
            if output_handle is not None:
                output_handle.write(recovered)
                output_handle.flush()
            elapsed_so_far = time.perf_counter() - transfer_start
            print(f"stream_output_bytes={progressive_bytes}")
            print(f"stream_elapsed_sec={elapsed_so_far:.3f}")
            print(f"stream_goodput_bps={progressive_bytes * 8.0 / max(elapsed_so_far, 1e-9):.0f}")
            print(f"file_batch_ok={batch_index + 1}")
            if batch_index + 1 < total_batches and args.inter_batch_sec > 0.0:
                time.sleep(float(args.inter_batch_sec))
    finally:
        if output_handle is not None:
            output_handle.close()
        if sdr is not None:
            destroy_iio_buffers(sdr)

    recovered_file = b"".join(recovered_parts)
    total_elapsed = time.perf_counter() - transfer_start
    output_crc = zlib.crc32(recovered_file) & 0xFFFFFFFF
    file_ok = recovered_file == data
    if file_ok and not args.write_progressive:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_bytes(recovered_file)
    if args.write_progressive and args.output_file.exists():
        recovered_file = args.output_file.read_bytes()
        output_crc = zlib.crc32(recovered_file) & 0xFFFFFFFF
        file_ok = recovered_file == data

    print(f"output_file={args.output_file if file_ok else ''}")
    print(f"output_bytes={len(recovered_file)}")
    print(f"input_crc32={input_crc}")
    print(f"output_crc32={output_crc}")
    print(f"file_batches_ok={len(recovered_parts)}")
    print(f"total_elapsed_sec={total_elapsed:.3f}")
    print(f"file_goodput_bps={len(recovered_file) * 8.0 / max(total_elapsed, 1e-9):.0f}")
    print(f"profile_batches={len(recovered_parts)}")
    for key, value in stage_totals.items():
        print(f"profile_sum_{key}={value:.3f}")
    print(f"profile_capture_attempts_total={capture_attempts_total}")
    print(f"profile_capture_attempts_avg={capture_attempts_total / max(len(recovered_parts), 1):.3f}")
    print(f"profile_context_recreates={context_recreates}")
    print(f"file_ok={str(file_ok).lower()}")
    return 0 if file_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
