# v0.2 SDR TS 视频流计划

本文档定义 `v0.1 SDR RF Loopback Video Demo` 之后的下一个工程里程碑。

v0.1 已证明 ANTSDR E310 能通过真实 RF 回环传输视频数据，链路包含 QPSK、分组 CRC、文件 CRC、MPEG-TS remux、segment 输出和 watcher 侧重建。

v0.2 的目标是把这个证明推进为可重复的准实时视频流演示。

## 目标

构建稳定的 TS-over-SDR 演示：

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

目标还不是最终低延迟视频 modem，而是一个可控的工程基线：可见播放、可记录时序指标、RF 行为可复现。

## 基线

从以下目录运行：

```powershell
D:\hp-laptop\E-band\python_sdr_loopback
```

当前 v0.1 命令：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_demo
```

成功输出应包含：

```text
ts_convert_ok=true
sdr_video_stream_ok=true
watch_ok=true
ts_watch_ok=true
ts_stream_ok=true
```

## 当前 RF 工作点

将以下参数作为 v0.2 起点：

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
Watch during transfer: true
Watch poll interval: 0.5 s
```

TS 模式下，批次边界应保持 188 字节 MPEG-TS 包对齐：

```text
requested batch bytes: 160000
effective TS-aligned bytes: 159988
159988 = 188 * 851
```

160k 工作点来自 RF 回环测试：

```text
160k: metrics_ok=true, capture_attempts_avg=1.000, context_recreates_total=0, stream_goodput_bps_avg ~= 619 kbps
200k: metrics_ok=true, but capture_attempts_avg=1.333, context_recreates_total=1, stream_goodput_bps_avg ~= 439 kbps
livewatch poll=0.5: metrics_ok=true, capture_attempts_avg=1.000, context_recreates_total=0, stream_goodput_bps_avg ~= 617 kbps
```

## v0.2 目标

1. 传输过程中显示正在增长的 recovered TS 文件，而不是只在传输完成后显示。
2. 运行时报告有意义的流时序指标。
3. 保留 segment CRC 和最终文件 CRC 检查。
4. 让演示能通过一条命令复现。
5. 识别 Python 调度、IIO 采集、modem 解码、重试/上下文重建之间真正的瓶颈。

## 非目标

1. 在流基线测量前，不重写已经工作的 modem。
2. 在 TS 演示稳定且可观测前，不追求最大码率。
3. v0.2 不要求 GNU Radio 或 C++。
4. 不把 MP4 当作流传输格式。MP4 可以作为输入文件，但 over-SDR 流应使用 MPEG-TS。

## 验收指标

每次严肃 v0.2 测试都记录：

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

建议初始验收目标：

```text
file_ok=true
watch_ok=true
ts_stream_ok=true
profile_capture_attempts_avg <= 1.25
profile_context_recreates <= 1 per full phone.mp4 run
file_goodput_bps >= 400000 on the phone.mp4 sample
```

这些是工程目标，不是物理层极限。原始 QPSK 比特率是 15 Mbps，但当前 Python 批处理管线的端到端 goodput 低得多。

## 推荐测量命令

一条命令运行 TS 流：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_v02_baseline
```

默认情况下，segment watcher 会在 SDR 传输前启动，使 `live.ts` 能随着 segment 到达而增长：

```text
watch_during_transfer=true
watch_poll_sec=0.5
playable_file=artifacts\ts_stream_v02_baseline\live.ts
```

强制使用旧的传输后 watcher 行为：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_v02_post --no-watch-during-transfer
```

降低 watcher 轮询压力但仍在 SDR 传输期间观察：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_livewatch_poll05 --watch-poll-sec 0.5
```

`--watch-poll-sec 0.5` 是当前默认值，因为它在测量中接近传输后 watcher 的吞吐，同时保留 live output。

第一个恢复的 TS segment 追加后自动打开 ffplay：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_player_demo --open-player
```

`--open-player` 默认使用 `--player-input pipe`。watcher 仍会写入 `live.ts` 用于验证，但 ffplay 会通过 stdin 接收 TS 字节，避免在增长文件过短时卡住。对比旧行为：

```powershell
python scripts\run_ts_video_stream.py --input-file phone.mp4 --work-dir artifacts\ts_stream_player_file --open-player --player-input file
```

每批次 metrics 写入：

```text
artifacts\ts_stream_v02_baseline\metrics.jsonl
```

汇总 metrics：

```powershell
python scripts\summarize_stream_metrics.py artifacts\ts_stream_v02_baseline\metrics.jsonl
```

对一条命令流程产生的 TS 文件做 benchmark。直接使用 TS 对齐大小：

```powershell
python scripts\benchmark_stream_transfer.py --input-file artifacts\ts_stream_v02_baseline\phone.ts --runs 5 --batch-bytes 159988 --artifact-prefix artifacts\bench_ts_159988 --quiet-run-output
```

如果 benchmark 不稳定，可和非 TS 样例命令对比：

```powershell
python scripts\benchmark_stream_transfer.py --input-file phone.mp4 --runs 5 --batch-bytes 160000 --artifact-prefix artifacts\bench_mp4_160k --quiet-run-output
```

## 实现任务

### 任务 1：播放提示

当 watcher 开始产生输出时，让 TS stream wrapper 打印清晰播放提示：

```text
playable_hint=true
playable_file=...
```

不需要自动启动播放器。

### 任务 2：滚动指标

新增机器可读的逐批次流指标文件：

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

首选输出：

```text
metrics.jsonl
```

CSV 可以之后再加，用于绘图。

### 任务 3：Watcher 作为显示桥

先保留 `watch_video_segments.py` 作为第一版显示桥。它应支持：

```text
append recovered segments
validate segment CRC
validate offsets
report current output size
report watch elapsed time
```

后续可选：

```text
UDP localhost output
named pipe output
auto-open ffplay/VLC
```

### 任务 4：Segment 时序

测量真正影响视频观感的时序：

```text
watch_first_segment_elapsed_sec
watch_inter_segment_gap_avg_sec
watch_inter_segment_gap_max_sec
```

这些指标能判断系统只是文件传输，还是接近流式演示。

### 任务 5：后端决策

只有当 v0.2 metrics 可重复后，再决定是否把热点路径移出 Python：

```text
keep Python orchestration + optimize IIO buffering
move modem decode to C++/Numba
move stream pipeline to GNU Radio
move hardware IO into a persistent service
```

该决策应基于实测瓶颈，而不是猜测。

## 已知约束

1. 当前 Python 流程是批处理导向的。
2. `profile_context_recreates` 和重复采集尝试会主导耗时。
3. 大型单次 capture 可能触发 IIO buffer 错误或 bad-address 失败。
4. TS segment 文件是字节流块，不是完整 HLS segment。
5. 当前 goodput 是数百 kbps，而原始 QPSK 是 15 Mbps。

## 完成定义

v0.2 完成时，一条命令应能：

1. 必要时将 MP4 转为 TS。
2. 通过 E310 RF 回环以 segments 传输 TS。
3. 构建增长中的输出 TS 文件。
4. 打印播放提示。
5. 保存滚动时序指标。
6. 验证 segment CRC 和最终 CRC。
7. 至少 5 次运行给出稳定 benchmark 汇总。

## v0.3 低延迟 TS 流原型

v0.2 证明可靠 TS 文件传输。v0.3 改变运行模型：

```text
ffmpeg low-bitrate MPEG-TS stdout
-> small TS byte chunks
-> SDR RF loopback
-> recovered TS bytes
-> ffplay stdin
```

第一目标不是最大吞吐，而是真正“像流”的演示。当前默认演示 profile：

```text
video bitrate: 250 kbps
scale height: 360
fps: 12
encoder preset: veryfast
chunk size: about 20 KB, TS-packet aligned
player input: pipe:0 with a small ffplay buffer
first-screen latency: a few seconds
```

这个 profile 来自 RF 回环测试：同码率下 `veryfast` 比 `ultrafast` 看起来更好且保持流畅，而 `superfast` 在本地测试中出现卡顿。

先从有界冒烟测试开始：

```powershell
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_20chunks --max-chunks 20
```

如果能打开并持续播放，移除 chunk 限制：

```powershell
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_stream
```

可用变体：

```powershell
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_300k --video-bitrate 300k --video-bufsize 600k --fps 15 --gop 15 --max-chunks 20
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_noplayer --no-player --max-chunks 20
```

如果播放完整但视觉上跳帧或有压缩伪影，RF 字节大概率是完整的，只是播放器收到的是突发 chunk，且编码器约束过紧。默认 profile 已经是更平滑的低码率 profile：

```powershell
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_smooth_250k --max-chunks 20
```

然后移除 `--max-chunks 20` 做更长运行：

```powershell
python scripts\run_low_latency_ts_stream.py --input-file small.mp4 --work-dir artifacts\v03_small_smooth_250k_full
```

关键字段：

```text
stream_chunk
stream_chunk_bytes
chunk_ok
stream_goodput_bps
stream_chunks_ok
stream_chunks_failed
low_latency_stream_ok
```

如果 `low_latency_stream_ok=true`，说明 RF 回环流路径处理了所有已处理 chunk。

## v0.4 最小 PC GUI 外壳

第一版 GUI 保持已验证的 Python SDR 管线不变，只用一个小 Tkinter 控制面板包装它：

```powershell
python scripts\sdr_video_gui.py
```

GUI 提供：

```text
Chinese labels
video file picker
work directory picker
Stable demo / Smoke test / Conservative / Quality try / No player debug presets
Start / Stop controls
source preview and receiver playback status
automatic player window sizing from video content
live log output
chunk, goodput, OK/failed, context recreate, elapsed status
```

默认 `Stable demo` 预设调用当前 v0.3 最佳流 profile：

```text
250k, 360p, 12fps, veryfast, 20 KB chunks, buffered ffplay pipe
```

它有意保持为薄外壳：从命令行 RF proof of concept 过渡到真正上位机应用，同时不扰动 SDR 链路。

需要区分两个显示尺寸概念：

```text
player window size: ffplay automatically follows the decoded video size
encoded video size: changes --scale-height; RF bitrate pressure increases
```

演示时保持播放器尺寸自动即可。提高编码分辨率应单独测试，因为当前 Python chunked SDR 路径可能会卡顿。

在 Windows 上，ffplay 嵌入依赖 SDL 构建，实际并不可靠。因此 GUI 使用两个独立播放器窗口，并设置明确中文标题与并排位置：`发送视频` 和 `接收视频`。
