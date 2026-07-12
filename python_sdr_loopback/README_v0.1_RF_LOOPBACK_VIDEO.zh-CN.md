# v0.1 SDR RF 回环视频演示

本文档将当前 ANTSDR E310 RF 回环视频演示固化为可复现的 v0.1 里程碑。

## 目标

使用一台 ANTSDR E310 同时作为发射端和接收端：

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

这是一个可工作的 RF 回环视频演示和协议原型，但还不是低延迟生产级视频 modem。

## 硬件

已知测试连接：

```text
ANTSDR E310 TX1
  -> coax
  -> 6 dB attenuator
  -> ANTSDR E310 RX1
```

重要注意：

```text
Do not connect TX directly to RX without attenuation.
```

当前已测试的端口设置：

```text
TX channel: 0
RX channel: 0
TX RF port: B
RX RF port: B_BALANCED
E310 URI: ip:192.168.1.10
```

## 软件要求

Python 包：

```powershell
python -m pip install pyadi-iio numpy matplotlib
```

系统工具：

```powershell
ffmpeg -version
```

如果 `ffmpeg` 不在 `PATH` 中，可通过 `--ffmpeg` 传入绝对路径。

## 当前 RF 参数

当前 v0.1 视频演示使用：

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

在 MPEG-TS 模式下，批次大小会自动按 188 字节 TS 包对齐：

```text
requested batch: 120000 bytes
effective batch: 119944 bytes
119944 = 188 * 638
```

## 一条命令运行演示

从以下目录运行：

```powershell
D:\hp-laptop\E-band\python_sdr_loopback
```

命令：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_demo
```

该命令会执行：

```text
phone.mp4
  -> ffmpeg remux to MPEG-TS
  -> SDR RF-loopback transfer
  -> segment_0001.bin, segment_0002.bin, ...
  -> manifest.json
  -> live.ts rebuild
  -> CRC validation
```

成功标志：

```text
ts_convert_ok=true
sdr_video_stream_ok=true
watch_ok=true
ts_watch_ok=true
ts_stream_ok=true
```

播放重建后的流：

```powershell
start artifacts\ts_stream_demo\live.ts
```

## 如果已经有 TS 文件

如果 `artifacts\phone.ts` 已存在：

```powershell
python scripts\run_ts_video_stream.py --input-file artifacts\phone.ts --skip-convert --work-dir artifacts\ts_stream_onecmd
```

播放：

```powershell
start artifacts\ts_stream_onecmd\live.ts
```

## 手动验证命令

检查 segment 完整性：

```powershell
python scripts\inspect_video_manifest.py --manifest-file artifacts\ts_stream_demo\segments\manifest.json --rebuild-file artifacts\ts_stream_demo\rebuilt.ts
```

期望结果：

```text
segment_errors=0
rebuilt_size_ok=true
rebuilt_crc_ok=true
inspect_ok=true
```

监听并重建已有 segments：

```powershell
python scripts\watch_video_segments.py --segment-dir artifacts\ts_stream_demo\segments --output-file artifacts\ts_stream_demo\live_from_watch.ts --timeout-sec 30
```

期望结果：

```text
watch_output_size_ok=true
watch_output_crc_ok=true
watch_ok=true
```

## 当前测量结果

一次有代表性的成功 TS 运行：

```text
file_ok=true
watch_ok=true
ts_stream_ok=true
profile_capture_attempts_avg=1.000
profile_context_recreates=0
total_elapsed_sec ~= 7.8 s
file_goodput_bps ~= 0.57 Mbps
```

物理层原始 QPSK 速率是 15 Mbps，但当前 Python/IIO 原型的实际 goodput 低得多，主要原因包括：

```text
IIO buffer setup and teardown
cyclic TX buffer upload
batch-based RX capture
Python demodulation and packet scan
occasional low-level RX retries
```

## v0.1 证明了什么

v0.1 证明：

```text
E310 TX/RX RF loopback works.
AD9361 configuration is usable from Python/pyadi-iio.
QPSK/RRC packet modem works at 15 Mbps raw symbol settings.
CRC-checked file recovery works.
MPEG-TS video can be sent through the SDR loopback.
Segments and manifest are recoverable and verifiable.
Rebuilt TS output can be played after recovery.
```

## 已知限制

这还不是真正的实时视频：

```text
The Python implementation is batch-oriented.
RX capture sometimes needs context retries.
Goodput is around hundreds of kbps, not close to the 15 Mbps raw rate.
MP4 is supported as input, but it is remuxed to MPEG-TS for stream-friendly behavior.
The current demo rebuilds playable TS output; it does not yet provide low-latency live playback.
```

最终上位机软件不要把高速 SDR DSP 路径直接放在 UI 线程中。

推荐的最终架构：

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

## 下一里程碑

建议 v0.2：

```text
Replace batch file transfer with continuous TS packet streaming.
Keep TS packets aligned to 188 bytes.
Add live player integration.
Add FEC or selective retransmission.
Move hot DSP/IIO path toward C/C++ or GNU Radio.
```
