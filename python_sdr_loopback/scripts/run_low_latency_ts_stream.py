from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from e310_rf_loopback import configure_sdr, destroy_iio_buffers, get_channel_attr, print_rx_stats  # noqa: E402
from run_file_transfer_stream import (  # noqa: E402
    build_batch_payloads,
    max_rx_buffer_size,
    modulate_batch_tx,
    recover_part_from_packets,
    rx_level_dbfs,
)
from sdr_loopback import ModemConfig, QpskLoopbackModem  # noqa: E402
from sdr_loopback.file_payload import FILE_HEADER_SIZE  # noqa: E402
from sdr_loopback.packet import Packet  # noqa: E402
from sdr_loopback.payload import payload_efficiency, raw_bitrate_bps  # noqa: E402


TS_PACKET_SIZE = 188


def ts_aligned_bytes(value: int) -> int:
    return max(TS_PACKET_SIZE, (int(value) // TS_PACKET_SIZE) * TS_PACKET_SIZE)


def ffmpeg_command(args, output: str) -> list[str]:
    command = [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if args.realtime_input:
        command.append("-re")
    command.extend(["-i", str(args.input_file)])
    filters: list[str] = []
    if args.scale_height > 0:
        filters.append(f"scale=-2:{args.scale_height}")
    if args.fps > 0:
        filters.append(f"fps={args.fps}")
    if args.copy_video:
        command.extend(["-c:v", "copy"])
    else:
        if filters:
            command.extend(["-vf", ",".join(filters)])
        command.extend(
            [
                "-c:v",
                args.video_codec,
                "-preset",
                args.encoder_preset,
                "-tune",
                "zerolatency",
                "-b:v",
                args.video_bitrate,
                "-maxrate",
                args.video_bitrate,
                "-bufsize",
                args.video_bufsize,
                "-g",
                str(args.gop),
                "-bf",
                "0",
            ]
        )
    if args.no_audio:
        command.append("-an")
    else:
        command.extend(["-c:a", "aac", "-b:a", args.audio_bitrate])
    command.extend(["-f", "mpegts", output])
    return command


def start_player(player: str, buffered: bool) -> subprocess.Popen:
    command = [
        player,
        "-flags",
        "low_delay",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-framedrop",
    ]
    if not buffered:
        command.extend(["-fflags", "nobuffer"])
    command.extend(
        [
        "-i",
        "pipe:0",
        ]
    )
    print(f"player_command={' '.join(command)}")
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def write_pipe(process: subprocess.Popen | None, data: bytes, label: str) -> bool:
    if process is None or process.stdin is None:
        return True
    try:
        process.stdin.write(data)
        process.stdin.flush()
        return True
    except (BrokenPipeError, OSError) as exc:
        print(f"{label}_pipe_closed=true")
        print(f"{label}_pipe_error={exc}")
        return False


def print_sdr_info(sdr, args) -> None:
    print(f"tx_channel={args.tx_channel}")
    print(f"rx_channel={args.rx_channel}")
    print(f"tx_rf_port_select={get_channel_attr(sdr, args.tx_channel, 'rf_port_select', True)}")
    print(f"rx_rf_port_select={get_channel_attr(sdr, args.rx_channel, 'rf_port_select', False)}")
    print(f"tx_hardwaregain_chan{args.tx_channel}={sdr._get_iio_attr(f'voltage{args.tx_channel}', 'hardwaregain', True)}")
    print(f"rx_hardwaregain_chan{args.rx_channel}={sdr._get_iio_attr(f'voltage{args.rx_channel}', 'hardwaregain', False)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-latency MPEG-TS stream over E310 RF loopback.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/low_latency_ts_stream"))
    parser.add_argument("--output-file", type=Path, help="Defaults to <work-dir>/recovered.ts.")
    parser.add_argument("--metrics-file", type=Path, help="Defaults to <work-dir>/metrics.jsonl.")
    parser.add_argument("--chunk-bytes", type=int, default=20_000)
    parser.add_argument("--max-chunks", type=int, default=0, help="0 means stream until ffmpeg EOF.")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--player", default="ffplay")
    parser.add_argument("--no-player", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--realtime-input", action="store_true", default=True)
    parser.add_argument("--no-realtime-input", action="store_false", dest="realtime_input")
    parser.add_argument("--copy-video", action="store_true")
    parser.add_argument("--video-codec", default="libx264")
    parser.add_argument("--video-bitrate", default="250k")
    parser.add_argument("--video-bufsize", default="500k")
    parser.add_argument("--encoder-preset", default="veryfast")
    parser.add_argument("--gop", type=int, default=12)
    parser.add_argument("--scale-height", type=int, default=360, help="0 keeps source height; e.g. 360 reduces artifacts.")
    parser.add_argument("--fps", type=int, default=12, help="0 keeps source FPS; e.g. 12 or 15 is smoother at low bitrate.")
    parser.set_defaults(player_buffered=True)
    parser.add_argument("--player-buffered", action="store_true", help="Allow a small ffplay buffer instead of strict nobuffer.")
    parser.add_argument("--player-nobuffer", action="store_false", dest="player_buffered", help="Use strict ffplay nobuffer mode.")
    parser.add_argument("--no-audio", action="store_true", default=True)
    parser.add_argument("--audio", action="store_false", dest="no_audio")
    parser.add_argument("--audio-bitrate", default="64k")
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
    parser.add_argument("--tx-settle-sec", type=float, default=0.20)
    parser.add_argument("--rx-discard-buffers", type=int, default=0)
    parser.add_argument("--tx-cyclic-copies", type=int, default=3)
    parser.add_argument("--rx-frame-copies", type=float, default=1.0)
    parser.add_argument("--min-rx-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--rx-level-retries", type=int, default=8)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    if not args.input_file.exists():
        print(f"input_exists=false")
        print(f"input_file={args.input_file}")
        return 2
    if args.chunk_bytes <= 0:
        print("--chunk-bytes must be positive", file=sys.stderr)
        return 2
    if args.payload_bytes <= FILE_HEADER_SIZE:
        print(f"--payload-bytes must be greater than {FILE_HEADER_SIZE}", file=sys.stderr)
        return 2
    if args.max_chunks < 0:
        print("--max-chunks must be non-negative", file=sys.stderr)
        return 2
    if args.rx_level_retries < 0:
        print("--rx-level-retries must be non-negative", file=sys.stderr)
        return 2

    args.work_dir.mkdir(parents=True, exist_ok=True)
    output_file = args.output_file or args.work_dir / "recovered.ts"
    metrics_file = args.metrics_file or args.work_dir / "metrics.jsonl"
    effective_chunk_bytes = ts_aligned_bytes(args.chunk_bytes)
    ffmpeg_path = shutil.which(args.ffmpeg)
    player_path = None if args.no_player else shutil.which(args.player)

    if ffmpeg_path is not None:
        args.ffmpeg = ffmpeg_path
    if player_path is not None:
        args.player = player_path

    encoder_command = ffmpeg_command(args, "pipe:1")
    print("low_latency_ts_stream=true")
    print(f"input_file={args.input_file}")
    print(f"work_dir={args.work_dir}")
    print(f"output_file={output_file}")
    print(f"metrics_file={metrics_file}")
    print(f"chunk_bytes_requested={args.chunk_bytes}")
    print(f"chunk_bytes_effective={effective_chunk_bytes}")
    print(f"ts_packet_aligned={str(effective_chunk_bytes % TS_PACKET_SIZE == 0).lower()}")
    print(f"video_bitrate={args.video_bitrate}")
    print(f"scale_height={args.scale_height}")
    print(f"fps={args.fps}")
    print(f"player_buffered={str(bool(args.player_buffered)).lower()}")
    print(f"copy_video={str(bool(args.copy_video)).lower()}")
    print(f"open_player={str(not args.no_player).lower()}")
    print(f"ffmpeg_command={' '.join(encoder_command)}")

    if args.dry_run:
        print("dry_run=true")
        return 0

    if ffmpeg_path is None:
        print("ffmpeg_found=false")
        return 3
    if not args.no_player and player_path is None:
        print("player_found=false")
        print(f"player={args.player}")
        return 3

    try:
        import adi
    except ImportError:
        print("pyadi-iio is not installed. Install with: python -m pip install pyadi-iio", file=sys.stderr)
        return 2

    metrics_file.write_text("", encoding="utf-8")
    modem = QpskLoopbackModem(
        ModemConfig(sample_rate=args.sample_rate, symbol_rate=args.symbol_rate, tx_amplitude=args.tx_amplitude)
    )
    rx_buffer_size, samples_per_packet, _max_chunks = max_rx_buffer_size(modem, args, effective_chunk_bytes)
    encoded_packet_size = len(Packet(sequence=1, payload=bytes(int(args.payload_bytes))).encode())
    bitrate_est = raw_bitrate_bps(float(args.symbol_rate)) * payload_efficiency(
        int(args.payload_bytes), encoded_packet_size, 8
    )
    print(f"payload_bitrate_est_bps={bitrate_est:.0f}")
    print(f"rx_buffer_used={rx_buffer_size}")
    print(f"samples_per_packet={samples_per_packet:.1f}")

    stream_start = time.perf_counter()
    chunks_ok = 0
    chunks_failed = 0
    output_bytes = 0
    capture_attempts_total = 0
    context_recreates = 0
    sdr = None
    ffmpeg_process = None
    player_process = None
    output_handle = None
    try:
        sdr = configure_sdr(adi, args, rx_buffer_size)
        print_sdr_info(sdr, args)
        ffmpeg_process = subprocess.Popen(encoder_command, stdout=subprocess.PIPE)
        player_process = None if args.no_player else start_player(args.player, bool(args.player_buffered))
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_handle = output_file.open("wb")

        chunk_index = 0
        while True:
            if args.max_chunks and chunk_index >= args.max_chunks:
                break
            if ffmpeg_process.stdout is None:
                print("ffmpeg_stdout_missing=true")
                return 3
            chunk = ffmpeg_process.stdout.read(effective_chunk_bytes)
            if not chunk:
                break
            chunk_index += 1
            chunk_start = time.perf_counter()
            chunk_context_start = context_recreates
            print(f"stream_chunk={chunk_index}")
            print(f"stream_chunk_bytes={len(chunk)}")

            payload_start = time.perf_counter()
            file_payloads, payloads = build_batch_payloads(chunk, args)
            payload_elapsed = time.perf_counter() - payload_start
            modulate_start = time.perf_counter()
            tx_padded = modulate_batch_tx(modem, payloads, args)
            modulate_elapsed = time.perf_counter() - modulate_start
            print(f"chunk_packets={len(file_payloads)}")
            print(f"chunk_tx_cyclic_samples={len(tx_padded)}")

            rx = None
            level = -999.0
            capture_start = time.perf_counter()
            chunk_attempts = 0
            for attempt in range(1, int(args.rx_level_retries) + 2):
                chunk_attempts += 1
                capture_attempts_total += 1
                try:
                    from e310_rf_loopback import capture_once  # noqa: PLC0415

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

            if rx is None or level < float(args.min_rx_rms_dbfs):
                chunks_failed += 1
                print(f"chunk_ok=false")
                if not args.continue_on_error:
                    break
                continue

            print_rx_stats(rx, float(args.adc_full_scale))
            decode_start = time.perf_counter()
            max_packets = len(file_payloads) * max(2, int(np.ceil(args.rx_frame_copies)) + 1)
            packets = modem.receive_many(rx, max_packets)
            decode_elapsed = time.perf_counter() - decode_start
            recovered, report = recover_part_from_packets(packets, file_payloads)
            chunk_ok = bool(report.get("file_ok")) and recovered == chunk
            print(f"packets_decoded={report.get('packets_decoded', 0)}")
            print(f"chunks_ok_in_chunk={report.get('chunks_ok', 0)}")
            print(f"missing_chunks={report.get('missing_chunks', [])}")
            print(f"chunk_ok={str(chunk_ok).lower()}")

            if chunk_ok:
                chunks_ok += 1
                output_handle.write(recovered)
                output_handle.flush()
                output_bytes += len(recovered)
                write_pipe(player_process, recovered, "player")
            else:
                chunks_failed += 1
                if not args.continue_on_error:
                    break

            elapsed = time.perf_counter() - stream_start
            goodput = output_bytes * 8.0 / max(elapsed, 1e-9)
            chunk_elapsed = time.perf_counter() - chunk_start
            print(f"chunk_elapsed_sec={chunk_elapsed:.3f}")
            print(f"stream_output_bytes={output_bytes}")
            print(f"stream_elapsed_sec={elapsed:.3f}")
            print(f"stream_goodput_bps={goodput:.0f}")
            with metrics_file.open("a", encoding="utf-8") as handle:
                handle.write(
                    (
                        f'{{"chunk_index":{chunk_index},"chunk_ok":{str(chunk_ok).lower()},'
                        f'"chunk_bytes":{len(chunk)},"chunk_elapsed_sec":{chunk_elapsed:.6f},'
                        f'"payload_build_elapsed_sec":{payload_elapsed:.6f},'
                        f'"modulate_elapsed_sec":{modulate_elapsed:.6f},'
                        f'"sdr_capture_elapsed_sec":{capture_elapsed:.6f},'
                        f'"decode_elapsed_sec":{decode_elapsed:.6f},'
                        f'"capture_attempts":{chunk_attempts},'
                        f'"context_recreates_this_batch":{context_recreates - chunk_context_start},'
                        f'"context_recreates_so_far":{context_recreates},'
                        f'"stream_output_bytes":{output_bytes},'
                        f'"stream_elapsed_sec":{elapsed:.6f},'
                        f'"stream_goodput_bps":{goodput:.3f}}}\n'
                    )
                )
    finally:
        if output_handle is not None:
            output_handle.close()
        if ffmpeg_process is not None:
            try:
                ffmpeg_process.terminate()
            except Exception:
                pass
        if player_process is not None and player_process.stdin is not None:
            try:
                player_process.stdin.close()
            except Exception:
                pass
        if sdr is not None:
            destroy_iio_buffers(sdr)

    total_elapsed = time.perf_counter() - stream_start
    print(f"stream_chunks_ok={chunks_ok}")
    print(f"stream_chunks_failed={chunks_failed}")
    print(f"stream_output_bytes={output_bytes}")
    print(f"stream_total_elapsed_sec={total_elapsed:.3f}")
    print(f"stream_goodput_bps={output_bytes * 8.0 / max(total_elapsed, 1e-9):.0f}")
    print(f"stream_capture_attempts_total={capture_attempts_total}")
    print(f"stream_context_recreates={context_recreates}")
    print(f"low_latency_stream_ok={str(chunks_ok > 0 and chunks_failed == 0).lower()}")
    return 0 if chunks_ok > 0 and chunks_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
