from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback import ModemConfig, QpskLoopbackModem
from sdr_loopback.file_payload import build_file_payloads, dewhiten_payload, parse_file_payload, recover_file, whiten_payload
from sdr_loopback.packet import Packet
from sdr_loopback.payload import build_payload, payload_efficiency, raw_bitrate_bps


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


def rx_level_dbfs(rx: np.ndarray, full_scale: float) -> float:
    magnitude = np.abs(rx)
    rms = float(np.sqrt(np.mean(magnitude * magnitude)))
    return dbfs(rms, full_scale)


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


def set_channel_attr(sdr, channel_index: int, attr_name: str, output: bool, value) -> None:
    sdr._set_iio_attr(f"voltage{channel_index}", attr_name, output, value)


def get_channel_attr(sdr, channel_index: int, attr_name: str, output: bool):
    return sdr._get_iio_attr_str(f"voltage{channel_index}", attr_name, output)


def configure_sdr(adi_module, args, rx_buffer_size: int):
    sdr = adi_module.ad9361(uri=args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_rf_bandwidth = int(args.bandwidth)
    sdr.tx_rf_bandwidth = int(args.bandwidth)
    sdr.rx_lo = int(args.lo_hz)
    sdr.tx_lo = int(args.lo_hz)
    sdr.rx_enabled_channels = [args.rx_channel]
    sdr.tx_enabled_channels = [args.tx_channel]
    sdr.rx_buffer_size = int(rx_buffer_size)
    set_channel_attr(sdr, args.rx_channel, "rf_port_select", False, args.rx_port)
    set_channel_attr(sdr, args.tx_channel, "rf_port_select", True, args.tx_port)
    set_channel_attr(sdr, args.rx_channel, "gain_control_mode", False, "manual")
    sdr._set_iio_attr_float(f"voltage{args.rx_channel}", "hardwaregain", False, float(args.rx_gain_db))
    sdr._set_iio_attr_float(f"voltage{args.tx_channel}", "hardwaregain", True, float(args.tx_gain_db))
    return sdr


def capture_once(sdr, tx_samples: np.ndarray, settle_sec: float, discard_buffers: int) -> np.ndarray:
    destroy_iio_buffers(sdr)
    sdr.tx_cyclic_buffer = True
    sdr.tx(tx_samples)
    time.sleep(float(settle_sec))
    for _ in range(discard_buffers):
        sdr.rx()
    raw = sdr.rx()
    return np.asarray(raw[0] if isinstance(raw, list) else raw, dtype=np.complex64)


def save_iq_capture(
    path: Path,
    tx: np.ndarray,
    rx: np.ndarray,
    args,
    packets_ok: int | None = None,
    payload_size: int | None = None,
    expected_payloads: list[bytes] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "tx": tx.astype(np.complex64),
        "rx": rx.astype(np.complex64),
        "sample_rate": float(args.sample_rate),
        "symbol_rate": float(args.symbol_rate),
        "lo_hz": int(args.lo_hz),
        "tx_gain_db": float(args.tx_gain_db),
        "rx_gain_db": float(args.rx_gain_db),
        "tx_amplitude": float(args.tx_amplitude),
        "tx_dac_scale": float(args.tx_dac_scale),
        "packet_count": int(args.packet_count),
        "packets_ok": -1 if packets_ok is None else int(packets_ok),
        "payload_mode": "file" if args.input_file else str(args.payload_pattern),
        "payload_bytes": -1 if payload_size is None else int(payload_size),
        "message": str(args.message),
    }
    if expected_payloads:
        fields["expected_payloads"] = np.vstack(
            [np.frombuffer(payload, dtype=np.uint8) for payload in expected_payloads]
        )
    np.savez_compressed(path, **fields)
    print(f"iq_capture={path}")


def save_plots(prefix: Path, modem: QpskLoopbackModem, rx: np.ndarray, sample_rate: float) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    prefix.parent.mkdir(parents=True, exist_ok=True)
    rx_no_dc = rx - np.mean(rx)
    window = np.hanning(len(rx_no_dc))
    spectrum = np.fft.fftshift(np.fft.fft(rx_no_dc * window))
    freq = np.fft.fftshift(np.fft.fftfreq(len(rx_no_dc), d=1.0 / sample_rate))
    power_db = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-12))
    power_db -= float(np.max(power_db))

    spectrum_path = prefix.with_name(prefix.name + "_spectrum.png")
    plt.figure(figsize=(9, 4.8))
    plt.plot(freq / 1e3, power_db, linewidth=1.0)
    plt.ylim(-90, 3)
    plt.xlabel("Frequency offset (kHz)")
    plt.ylabel("Relative power (dB)")
    plt.title("RX Spectrum")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(spectrum_path, dpi=150)
    plt.close()

    points = modem.constellation_points(rx, max_points=2500)
    constellation_path = prefix.with_name(prefix.name + "_constellation.png")
    plt.figure(figsize=(5.4, 5.4))
    if len(points):
        plt.scatter(points.real, points.imag, s=5, alpha=0.35)
    plt.axhline(0, color="black", linewidth=0.6, alpha=0.4)
    plt.axvline(0, color="black", linewidth=0.6, alpha=0.4)
    plt.xlabel("I")
    plt.ylabel("Q")
    plt.title("Matched QPSK Constellation")
    plt.grid(True, alpha=0.3)
    plt.axis("equal")
    plt.tight_layout()
    plt.savefig(constellation_path, dpi=150)
    plt.close()

    print(f"spectrum_plot={spectrum_path}")
    print(f"constellation_plot={constellation_path}")


def print_packet_preview(packet: Packet, payload_pattern: str) -> None:
    print(f"sequence={packet.sequence}")
    print(f"payload_len={len(packet.payload)}")
    if payload_pattern == "message":
        print(f"payload={packet.payload.decode('utf-8', errors='replace')}")
    else:
        print(f"payload_hex_prefix={packet.payload[:16].hex()}")


def main() -> int:
    script_start = time.perf_counter()
    parser = argparse.ArgumentParser(description="Transmit and receive one QPSK packet through E310 RF loopback.")
    parser.add_argument("--uri", default="ip:192.168.1.10")
    parser.add_argument("--message", default="hello rf")
    parser.add_argument(
        "--payload-bytes",
        type=int,
        help="Payload size per packet for counter/random patterns. Omit to send --message.",
    )
    parser.add_argument(
        "--payload-pattern",
        choices=("message", "counter", "random"),
        default="message",
        help="Payload content pattern. Use counter/random with --payload-bytes for data-rate tests.",
    )
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
    parser.add_argument("--packet-count", type=int, default=1, help="Number of sequenced packets to transmit and verify.")
    parser.add_argument("--input-file", type=Path, help="Transmit this file as fixed-size file payload chunks.")
    parser.add_argument("--output-file", type=Path, help="Write recovered file bytes here when --input-file is used.")
    parser.add_argument(
        "--file-guard-packets",
        type=int,
        default=16,
        help="Dummy packets placed before file chunks to absorb cyclic TX boundary transients.",
    )
    parser.add_argument(
        "--preflight-rx-level",
        action="store_true",
        help="Probe RX level with a small packet before uploading a large file batch.",
    )
    parser.add_argument(
        "--tx-settle-sec",
        type=float,
        default=0.25,
        help="Seconds to wait after starting cyclic TX before RX capture.",
    )
    parser.add_argument(
        "--rx-discard-buffers",
        type=int,
        default=0,
        help="Number of RX buffers to discard before the measured capture.",
    )
    parser.add_argument(
        "--tx-cyclic-copies",
        type=int,
        default=3,
        help="Number of frame copies uploaded into the cyclic TX buffer.",
    )
    parser.add_argument(
        "--min-rx-rms-dbfs",
        type=float,
        help="Retry TX/RX capture if RX RMS is below this dBFS threshold.",
    )
    parser.add_argument(
        "--rx-level-retries",
        type=int,
        default=0,
        help="Extra TX/RX capture attempts when --min-rx-rms-dbfs is not met.",
    )
    parser.add_argument("--save-iq", type=Path, help="Save TX/RX IQ and run metadata to a compressed .npz file.")
    parser.add_argument("--plot-prefix", type=Path, help="Save RX spectrum and constellation PNGs with this path prefix.")
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
    if args.packet_count <= 0:
        print("--packet-count must be positive", file=sys.stderr)
        return 2
    if args.tx_settle_sec < 0.0:
        print("--tx-settle-sec must be non-negative", file=sys.stderr)
        return 2
    if args.rx_discard_buffers < 0:
        print("--rx-discard-buffers must be non-negative", file=sys.stderr)
        return 2
    if args.tx_cyclic_copies <= 0:
        print("--tx-cyclic-copies must be positive", file=sys.stderr)
        return 2
    if args.rx_level_retries < 0:
        print("--rx-level-retries must be non-negative", file=sys.stderr)
        return 2
    if args.file_guard_packets < 0:
        print("--file-guard-packets must be non-negative", file=sys.stderr)
        return 2
    if not args.input_file and args.payload_pattern == "message" and args.payload_bytes is not None:
        print("--payload-bytes requires --payload-pattern counter or random", file=sys.stderr)
        return 2
    if not args.input_file and args.payload_pattern != "message" and args.payload_bytes is None:
        print("--payload-pattern counter/random requires --payload-bytes", file=sys.stderr)
        return 2
    if args.input_file and args.payload_pattern != "message":
        print("--input-file cannot be combined with --payload-pattern counter/random", file=sys.stderr)
        return 2
    if args.input_file and args.payload_bytes is None:
        args.payload_bytes = 512
    if args.payload_bytes is not None and not 0 <= args.payload_bytes <= 65535:
        print("--payload-bytes must be in [0, 65535]", file=sys.stderr)
        return 2

    payload_build_start = time.perf_counter()
    modem = QpskLoopbackModem(
        ModemConfig(
            sample_rate=args.sample_rate,
            symbol_rate=args.symbol_rate,
            tx_amplitude=args.tx_amplitude,
        )
    )
    message = args.message.encode("utf-8")
    file_payloads: list[bytes] = []
    if args.input_file:
        file_data = args.input_file.read_bytes()
        payload_size = int(args.payload_bytes)
        file_payloads = build_file_payloads(file_data, payload_size)
        wire_file_payloads = [whiten_payload(payload) for payload in file_payloads]
        guard_payloads = [bytes([0x55]) * payload_size for _ in range(args.file_guard_packets)]
        payloads = guard_payloads + wire_file_payloads
        args.packet_count = len(payloads)
        payload_pattern_label = "file"
        print(f"input_file={args.input_file}")
    else:
        payload_size = len(message) if args.payload_pattern == "message" else int(args.payload_bytes)
        payloads = [
            build_payload(sequence, payload_size, args.payload_pattern, message)
            for sequence in range(1, args.packet_count + 1)
        ]
        payload_pattern_label = args.payload_pattern
    payload_build_elapsed = time.perf_counter() - payload_build_start
    modulate_start = time.perf_counter()
    tx = np.concatenate(
        [
            modem.transmit(payload, sequence=sequence)
            for sequence, payload in enumerate(payloads, start=1)
        ]
    )
    encoded_packet_size = len(Packet(sequence=1, payload=payloads[0]).encode())
    preamble_size = 8
    raw_bitrate = raw_bitrate_bps(float(args.symbol_rate))
    efficiency = payload_efficiency(payload_size, encoded_packet_size, preamble_size)
    samples_per_packet = len(tx) / args.packet_count
    rx_frame_copies = 2 if args.input_file else 1
    min_rx_buffer = int(np.ceil(len(tx) * rx_frame_copies + samples_per_packet * 2))
    rx_buffer_size = max(args.rx_buffer, min_rx_buffer)
    tx *= float(args.tx_dac_scale)
    tx_padded = np.tile(tx, int(args.tx_cyclic_copies)).astype(np.complex64)
    preflight_enabled = bool(args.preflight_rx_level and args.input_file and args.min_rx_rms_dbfs is not None)
    preflight_payload = build_payload(1, min(payload_size, 512), "random")
    preflight_tx = modem.transmit(preflight_payload, sequence=1) * float(args.tx_dac_scale)
    preflight_tx_padded = np.tile(preflight_tx, int(args.tx_cyclic_copies)).astype(np.complex64)
    preflight_rx_buffer_size = max(args.rx_buffer, int(np.ceil(len(preflight_tx) * 2)))
    modulate_elapsed = time.perf_counter() - modulate_start
    print_tx_stats(tx)
    print(f"tx_cyclic_samples={len(tx_padded)}")
    print(f"samples_per_packet={samples_per_packet:.1f}")
    print(f"payload_pattern={payload_pattern_label}")
    print(f"payload_bytes={payload_size}")
    if args.input_file:
        print(f"file_guard_packets={args.file_guard_packets}")
        print(f"file_chunks_expected={len(file_payloads)}")
        print("file_whitening=true")
    print(f"raw_bitrate_bps={raw_bitrate:.0f}")
    print(f"payload_efficiency={efficiency:.6f}")
    print(f"payload_bitrate_est_bps={raw_bitrate * efficiency:.0f}")
    print(f"tx_settle_sec={args.tx_settle_sec:.3f}")
    print(f"rx_discard_buffers={args.rx_discard_buffers}")
    print(f"tx_cyclic_copies={args.tx_cyclic_copies}")
    print(f"rx_frame_copies={rx_frame_copies}")
    if args.min_rx_rms_dbfs is not None:
        print(f"min_rx_rms_dbfs={args.min_rx_rms_dbfs:.2f}")
        print(f"rx_level_retries={args.rx_level_retries}")
    print(f"rx_buffer_requested={args.rx_buffer}")
    print(f"rx_buffer_used={rx_buffer_size}")
    if preflight_enabled:
        print(f"preflight_enabled=true")
        print(f"preflight_rx_buffer_used={preflight_rx_buffer_size}")
    print(f"payload_build_elapsed_sec={payload_build_elapsed:.3f}")
    print(f"modulate_elapsed_sec={modulate_elapsed:.3f}")

    rx = None
    max_attempts = int(args.rx_level_retries) + 1
    sdr_capture_start = time.perf_counter()
    capture_attempts_used = 0
    try:
        for capture_attempt in range(1, max_attempts + 1):
            capture_attempts_used = capture_attempt
            attempt_start = time.perf_counter()
            sdr = configure_sdr(adi, args, preflight_rx_buffer_size if preflight_enabled else rx_buffer_size)
            configure_elapsed = time.perf_counter() - attempt_start
            if capture_attempt == 1:
                print(f"tx_channel={args.tx_channel}")
                print(f"rx_channel={args.rx_channel}")
                print(f"tx_rf_port_select={get_channel_attr(sdr, args.tx_channel, 'rf_port_select', True)}")
                print(f"rx_rf_port_select={get_channel_attr(sdr, args.rx_channel, 'rf_port_select', False)}")
                print(f"tx_hardwaregain_chan{args.tx_channel}={sdr._get_iio_attr(f'voltage{args.tx_channel}', 'hardwaregain', True)}")
                print(f"rx_hardwaregain_chan{args.rx_channel}={sdr._get_iio_attr(f'voltage{args.rx_channel}', 'hardwaregain', False)}")
            else:
                print(f"rx_context_retry={capture_attempt}")
            io_start = time.perf_counter()
            try:
                if preflight_enabled:
                    preflight_start = time.perf_counter()
                    preflight_rx = capture_once(sdr, preflight_tx_padded, float(args.tx_settle_sec), args.rx_discard_buffers)
                    preflight_level = rx_level_dbfs(preflight_rx, float(args.adc_full_scale))
                    print(f"preflight_elapsed_sec={time.perf_counter() - preflight_start:.3f}")
                    print(f"preflight_rx_rms_dbfs={preflight_level:.2f}")
                    if preflight_level < float(args.min_rx_rms_dbfs):
                        rx = preflight_rx
                    else:
                        real_start = time.perf_counter()
                        destroy_iio_buffers(sdr)
                        sdr.rx_buffer_size = int(rx_buffer_size)
                        rx = capture_once(sdr, tx_padded, float(args.tx_settle_sec), args.rx_discard_buffers)
                        print(f"real_capture_elapsed_sec={time.perf_counter() - real_start:.3f}")
                else:
                    rx = capture_once(sdr, tx_padded, float(args.tx_settle_sec), args.rx_discard_buffers)
            finally:
                destroy_iio_buffers(sdr)
            io_elapsed = time.perf_counter() - io_start
            print(f"capture_attempt={capture_attempt}")
            print(f"sdr_configure_elapsed_sec={configure_elapsed:.3f}")
            print(f"sdr_io_elapsed_sec={io_elapsed:.3f}")
            level = rx_level_dbfs(rx, float(args.adc_full_scale))
            if args.min_rx_rms_dbfs is None or level >= float(args.min_rx_rms_dbfs):
                break
            print(f"rx_level_retry={capture_attempt}")
            print(f"rx_rms_dbfs_attempt={level:.2f}")
            time.sleep(0.4)
    except OSError as exc:
        print(f"iio_error={exc}", file=sys.stderr)
        print(
            "IIO RX buffer is busy. Close GNU Radio/IIO tools/other Python runs, "
            "then power-cycle or reboot the E310 if the busy state persists.",
            file=sys.stderr,
        )
        return 3
    sdr_capture_elapsed = time.perf_counter() - sdr_capture_start

    if rx is None:
        print("rx_capture_failed=true", file=sys.stderr)
        return 3
    print(f"sdr_capture_elapsed_sec={sdr_capture_elapsed:.3f}")
    print(f"capture_attempts_used={capture_attempts_used}")
    print_rx_stats(rx, float(args.adc_full_scale))
    if args.stats_only:
        if args.save_iq:
            save_iq_capture(args.save_iq, tx, rx, args, payload_size=payload_size, expected_payloads=payloads)
        if args.plot_prefix:
            save_plots(args.plot_prefix, modem, rx, float(args.sample_rate))
        return 0

    decode_start = time.perf_counter()
    max_decode_packets = args.packet_count * rx_frame_copies
    packets = modem.receive_many(rx, max_decode_packets)
    decode_elapsed = time.perf_counter() - decode_start
    recover_start = time.perf_counter()
    recovered_payloads: list[bytes] = []
    if args.input_file:
        seen_chunks: set[int] = set()
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
        packets_ok = len(seen_chunks)
        expected_packets = len(file_payloads)
    else:
        good_sequences = {
            packet.sequence
            for packet in packets
            if 1 <= packet.sequence <= args.packet_count and packet.payload == payloads[packet.sequence - 1]
        }
        packets_ok = len(good_sequences)
        expected_packets = args.packet_count
    recover_elapsed = time.perf_counter() - recover_start
    for packet in packets[:10]:
        print_packet_preview(packet, payload_pattern_label)

    capture_capacity = min(expected_packets, int(len(rx) / samples_per_packet))
    packet_error_rate = 1.0 - packets_ok / max(capture_capacity, 1)
    print(f"packets_expected={expected_packets}")
    print(f"packets_capture_capacity={capture_capacity}")
    print(f"packets_decoded={len(packets)}")
    print(f"packets_ok={packets_ok}")
    print(f"packet_error_rate={packet_error_rate:.6f}")
    print(f"payload_bytes_ok={packets_ok * payload_size}")
    print(f"payload_bits_ok={packets_ok * payload_size * 8}")
    print(f"payload_bitrate_ok_bps={raw_bitrate * efficiency * packets_ok / max(capture_capacity, 1):.0f}")
    file_ok = False
    file_recover_elapsed = 0.0
    if args.input_file:
        file_recover_start = time.perf_counter()
        if recovered_payloads:
            recovered, file_report = recover_file(recovered_payloads)
            for key, value in file_report.items():
                print(f"{key}={value}")
            file_ok = file_report.get("file_crc32") == file_report.get("file_crc32_actual")
        else:
            recovered = b""
            file_ok = False
        file_recover_elapsed = time.perf_counter() - file_recover_start
        print(f"file_ok={str(file_ok).lower()}")
        if args.output_file and file_ok:
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_bytes(recovered)
            print(f"output_file={args.output_file}")
    if args.packet_count == 1 and packets_ok == 1:
        print("crc_ok=true")
    if args.save_iq:
        save_iq_capture(
            args.save_iq,
            tx,
            rx,
            args,
            packets_ok=packets_ok,
            payload_size=payload_size,
            expected_payloads=payloads,
        )
    if args.plot_prefix:
        save_plots(args.plot_prefix, modem, rx, float(args.sample_rate))
    print(f"decode_elapsed_sec={decode_elapsed:.3f}")
    print(f"packet_filter_elapsed_sec={recover_elapsed:.3f}")
    if args.input_file:
        print(f"file_recover_elapsed_sec={file_recover_elapsed:.3f}")
    print(f"script_total_elapsed_sec={time.perf_counter() - script_start:.3f}")
    if args.input_file:
        return 0 if file_ok else 1
    return 0 if packets_ok == args.packet_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
