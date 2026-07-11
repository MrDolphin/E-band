# v0.2 SDR TS Video Stream Plan

This document defines the next engineering milestone after `v0.1 SDR RF Loopback Video Demo`.

v0.1 proves that the ANTSDR E310 can transfer video data through a real RF loopback with QPSK, packet CRC, file CRC, MPEG-TS remuxing, segment output, and watcher-side rebuild.

v0.2 should turn that proof into a repeatable quasi-realtime video streaming demo.

## Target

Build a stable TS-over-SDR demo:

```text
video source
  -> MPEG-TS byte stream
  -> TS-packet-aligned SDR payload batches
  -> ANTSDR E310 AD9361 TX
  -> external RF loopback
  -> ANTSDR E310 AD9361 RX
  -> recovered segment files
  -> growing live.ts
  -> local playback / display
```

The goal is not yet a final low-latency video modem. The goal is a controlled engineering baseline with visible playback, timing metrics, and reproducible RF behavior.

## Baseline

Run from:

```powershell
D:\hp-laptop\E-band\python_sdr_loopback
```

Current v0.1 command:

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_demo
```

Successful output should include:

```text
ts_convert_ok=true
sdr_video_stream_ok=true
watch_ok=true
ts_watch_ok=true
ts_stream_ok=true
```

## Current RF Work Point

Use this as the v0.2 starting point:

```text
E310 URI: ip:192.168.1.10
LO frequency: 900 MHz
Sample rate: 30 MSPS
Symbol rate: 7.5 Msym/s
Raw QPSK bitrate: 15 Mbps
RF bandwidth: 20 MHz
TX gain: -12 dB
RX gain: 10 dB
TX channel: 0
RX channel: 0
TX RF port: B
RX RF port: B_BALANCED
TX amplitude: 0.5
TX DAC scale: 8192
Payload bytes: 512
TX cyclic copies: 3
RX discard buffers: 0
Minimum RX level: -45 dBFS
RX level retries: 8
Batch bytes: 160000
Inter-batch delay: 0.25 s
RX frame copies: 1.0
```

For TS mode, keep batch boundaries aligned to 188-byte MPEG-TS packets:

```text
requested batch bytes: 160000
effective TS-aligned bytes: 159988
159988 = 188 * 851
```

This 160k work point was selected from RF-loopback measurements:

```text
160k: metrics_ok=true, capture_attempts_avg=1.000, context_recreates_total=0, stream_goodput_bps_avg ~= 619 kbps
200k: metrics_ok=true, but capture_attempts_avg=1.333, context_recreates_total=1, stream_goodput_bps_avg ~= 439 kbps
```

## v0.2 Goals

1. Show a growing recovered TS file during transfer, not only after transfer completes.
2. Report meaningful stream timing while the transfer is running.
3. Keep segment CRC and final file CRC checks.
4. Make the demo reproducible with one command.
5. Identify the real bottleneck between Python orchestration, IIO capture, modem decode, and retry/context recreation.

## Non-Goals

1. Do not rewrite the working modem before the stream baseline is measured.
2. Do not chase maximum bitrate before the TS demo is stable and observable.
3. Do not require GNU Radio or C++ for v0.2.
4. Do not treat MP4 as the streaming transport. MP4 may be the input file, but the over-SDR stream should be MPEG-TS.

## Acceptance Metrics

Record these on every serious v0.2 test:

```text
file_ok
watch_ok
ts_stream_ok
first_segment_elapsed_sec
total_elapsed_sec
file_goodput_bps
profile_capture_attempts_avg
profile_context_recreates
profile_sum_sdr_capture_elapsed_sec
profile_sum_decode_elapsed_sec
watch_output_crc_ok
```

Suggested initial acceptance target:

```text
file_ok=true
watch_ok=true
ts_stream_ok=true
profile_capture_attempts_avg <= 1.25
profile_context_recreates <= 1 per full phone.mp4 run
file_goodput_bps >= 400000 on the phone.mp4 sample
```

These are engineering targets, not physical-layer limits. The raw QPSK bitrate is 15 Mbps, but the current Python batch pipeline has much lower end-to-end goodput.

## Recommended Measurement Commands

One-command TS stream:

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_v02_baseline
```

This writes per-batch metrics to:

```text
artifacts\ts_stream_v02_baseline\metrics.jsonl
```

Summarize the metrics:

```powershell
python scripts\summarize_stream_metrics.py artifacts\ts_stream_v02_baseline\metrics.jsonl
```

Benchmark the TS file produced by the one-command flow. Use the TS-aligned size directly:

```powershell
python scripts\benchmark_stream_transfer.py --input-file artifacts\ts_stream_v02_baseline\phone.ts --runs 5 --batch-bytes 159988 --artifact-prefix artifacts\bench_ts_159988 --quiet-run-output
```

If this benchmark is unstable, compare with the non-TS sample command:

```powershell
python scripts\benchmark_stream_transfer.py --input-file phone.mp4 --runs 5 --batch-bytes 160000 --artifact-prefix artifacts\bench_mp4_160k --quiet-run-output
```

## Implementation Tasks

### Task 1: Playback Hint

Make the TS stream wrapper print a clear playback hint when the watcher starts producing output:

```text
playable_hint=true
playable_file=...
```

This does not need to launch a player automatically.

### Task 2: Rolling Metrics

Add per-batch stream metrics in a machine-readable file:

```text
batch_index
batch_bytes
batch_elapsed_sec
sdr_capture_elapsed_sec
decode_elapsed_sec
rx_rms_dbfs
chunks_ok
missing_chunks
context_recreates_so_far
stream_output_bytes
stream_elapsed_sec
stream_goodput_bps
```

Preferred output:

```text
metrics.jsonl
```

CSV can be added later if needed for plotting.

### Task 3: Watcher-as-Display Bridge

Keep `watch_video_segments.py` as the first display bridge. It should support:

```text
append recovered segments
validate segment CRC
validate offsets
report current output size
report watch elapsed time
```

Optional later:

```text
UDP localhost output
named pipe output
auto-open ffplay/VLC
```

### Task 4: Segment Timing

Measure timing that matters for video perception:

```text
first segment available time
time between segment arrivals
max inter-segment gap
average inter-segment gap
```

This tells us whether the system is merely transferring files or approaching a stream-like demo.

### Task 5: Backend Decision

Only after v0.2 metrics are repeatable, decide whether to move hot paths out of Python:

```text
keep Python orchestration + optimize IIO buffering
move modem decode to C++/Numba
move stream pipeline to GNU Radio
move hardware IO into a persistent service
```

The decision should be based on measured bottlenecks, not guesswork.

## Known Constraints

1. The current Python flow is batch-oriented.
2. `profile_context_recreates` and repeated capture attempts can dominate elapsed time.
3. Large single captures can trigger IIO buffer errors or bad-address failures.
4. TS segment files are byte-stream chunks, not full HLS segments.
5. Current goodput is hundreds of kbps, while raw QPSK is 15 Mbps.

## Definition of Done

v0.2 is complete when one command can:

1. Convert MP4 to TS if needed.
2. Transfer TS over the E310 RF loopback in segments.
3. Build a growing output TS file.
4. Print playback hints.
5. Save rolling timing metrics.
6. Verify segment CRCs and final CRC.
7. Report a stable benchmark summary across at least five runs.
