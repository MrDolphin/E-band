# v0.1 SDR RF Loopback Video Demo

This document freezes the current ANTSDR E310 RF-loopback video demo as a reproducible v0.1 milestone.

## Goal

Use one ANTSDR E310 as both transmitter and receiver:

```text
PC Python
  -> packet + CRC32
  -> QPSK + RRC
  -> AD9361 TX
  -> external RF loopback
  -> AD9361 RX
  -> QPSK receive + CRC32
  -> MPEG-TS video segments
  -> live.ts rebuild + CRC check
```

This is a working RF-loopback video demo and protocol prototype. It is not yet a low-latency production video modem.

## Hardware

Known setup:

```text
ANTSDR E310 TX1
  -> coax
  -> 6 dB attenuator
  -> ANTSDR E310 RX1
```

Important:

```text
Do not connect TX directly to RX without attenuation.
```

Current tested port settings:

```text
TX channel: 0
RX channel: 0
TX RF port: B
RX RF port: B_BALANCED
E310 URI: ip:192.168.1.10
```

## Software Requirements

Python packages:

```powershell
python -m pip install pyadi-iio numpy matplotlib
```

System tools:

```powershell
ffmpeg -version
```

If `ffmpeg` is not in `PATH`, pass its absolute path with `--ffmpeg`.

## Current RF Parameters

The current v0.1 video demo uses:

```text
LO frequency: 900 MHz
Sample rate: 30 MSPS
Symbol rate: 7.5 Msym/s
Raw QPSK bitrate: 15 Mbps
RF bandwidth: 20 MHz
TX gain: -12 dB
RX gain: 10 dB
TX amplitude: 0.5
TX DAC scale: 8192
Payload bytes: 512
TX cyclic copies: 3
RX discard buffers: 0
Minimum RX level: -45 dBFS
RX level retries: 8
```

For MPEG-TS mode, batch size is automatically aligned to 188-byte TS packets:

```text
requested batch: 120000 bytes
effective batch: 119944 bytes
119944 = 188 * 638
```

## One-Command Demo

From:

```powershell
D:\hp-laptop\E-band\python_sdr_loopback
```

Run:

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_demo
```

This performs:

```text
phone.mp4
  -> ffmpeg remux to MPEG-TS
  -> SDR RF-loopback transfer
  -> segment_0001.bin, segment_0002.bin, ...
  -> manifest.json
  -> live.ts rebuild
  -> CRC validation
```

Success markers:

```text
ts_convert_ok=true
sdr_video_stream_ok=true
watch_ok=true
ts_watch_ok=true
ts_stream_ok=true
```

Play the rebuilt stream:

```powershell
start artifacts\ts_stream_demo\live.ts
```

## If You Already Have a TS File

If `artifacts\phone.ts` already exists:

```powershell
python scripts\run_ts_video_stream.py --input-file artifacts\phone.ts --skip-convert --work-dir artifacts\ts_stream_onecmd
```

Play:

```powershell
start artifacts\ts_stream_onecmd\live.ts
```

## Manual Verification Commands

Inspect segment integrity:

```powershell
python scripts\inspect_video_manifest.py --manifest-file artifacts\ts_stream_demo\segments\manifest.json --rebuild-file artifacts\ts_stream_demo\rebuilt.ts
```

Expected:

```text
segment_errors=0
rebuilt_size_ok=true
rebuilt_crc_ok=true
inspect_ok=true
```

Watch and rebuild existing segments:

```powershell
python scripts\watch_video_segments.py --segment-dir artifacts\ts_stream_demo\segments --output-file artifacts\ts_stream_demo\live_from_watch.ts --timeout-sec 30
```

Expected:

```text
watch_output_size_ok=true
watch_output_crc_ok=true
watch_ok=true
```

## Current Measured Results

Representative successful TS run:

```text
file_ok=true
watch_ok=true
ts_stream_ok=true
profile_capture_attempts_avg=1.000
profile_context_recreates=0
total_elapsed_sec ~= 7.8 s
file_goodput_bps ~= 0.57 Mbps
```

The physical-layer raw QPSK rate is 15 Mbps, but the current Python/IIO prototype goodput is much lower because of:

```text
IIO buffer setup and teardown
cyclic TX buffer upload
batch-based RX capture
Python demodulation and packet scan
occasional low-level RX retries
```

## What v0.1 Proves

v0.1 proves:

```text
E310 TX/RX RF loopback works.
AD9361 configuration is usable from Python/pyadi-iio.
QPSK/RRC packet modem works at 15 Mbps raw symbol settings.
CRC-checked file recovery works.
MPEG-TS video can be sent through the SDR loopback.
Segments and manifest are recoverable and verifiable.
Rebuilt TS output can be played after recovery.
```

## Known Limitations

This is not yet true real-time video:

```text
The Python implementation is batch-oriented.
RX capture sometimes needs context retries.
Goodput is around hundreds of kbps, not close to the 15 Mbps raw rate.
MP4 is supported as input, but it is remuxed to MPEG-TS for stream-friendly behavior.
The current demo rebuilds playable TS output; it does not yet provide low-latency live playback.
```

For final upper-computer software, do not put the high-rate SDR DSP path directly in the UI thread.

Recommended final architecture:

```text
UI application
  -> controls settings, status, playback

SDR backend process or library
  -> C/C++ or GNU Radio/libiio continuous TX/RX
  -> modem, framing, FEC/retry, statistics

Video pipeline
  -> TS/HLS/RTP-like stream format
  -> decoder/player
```

## Next Milestone

Suggested v0.2:

```text
Replace batch file transfer with continuous TS packet streaming.
Keep TS packets aligned to 188 bytes.
Add live player integration.
Add FEC or selective retransmission.
Move hot DSP/IIO path toward C/C++ or GNU Radio.
```

