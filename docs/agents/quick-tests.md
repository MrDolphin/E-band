# E 波段通感系统：快速复测命令

在 `D:\hp-laptop\E-band-fmcw\python_sdr_loopback` 目录运行。命令会访问 E310 或发射信号时，必须先确认物理链路已连接、允许发射，并且雷达 GUI 与 `/tmp/udp_receiver_fmcw` 已停止。

## 0. 启动前检查

E310 shell：

```sh
ps | grep -E 'iiod|udp_receiver_fmcw|video_modem_bridge' | grep -v grep
iio_attr -i -c ad9361-phy voltage0 sampling_frequency
iio_attr -o -c ad9361-phy voltage0 sampling_frequency
iio_attr -i -c ad9361-phy voltage0 rf_bandwidth
iio_attr -o -c ad9361-phy voltage0 rf_bandwidth
iio_attr -c ad9361-phy altvoltage0 frequency
iio_attr -c ad9361-phy altvoltage1 frequency
```

期望：`iiod` 存在；FMCW Python/GUI 测试期间无 UDP bridge 占用；RX/TX 采样率、带宽和 LO 与本次预设一致。

## 1. DDS 发射是否持续

示波器接 E310 TX1，先使用此命令判断 E310 基础循环发射：

```powershell
python scripts\diagnose_e310_tx.py --phase dds-tx-only --tx-channel 0 --tx-port B --cycles 1 --on-s 30 --off-s 1
```

预期：在 30 秒 `ON` 期间示波器保持稳定载波。只闪一下或无信号时，先不要进行雷达算法、角反或视频测试。

## 2. FMCW 发射是否持续

```powershell
python scripts\diagnose_e310_tx.py --phase fmcw-tx-only --tx-channel 0 --tx-port B --reuse-buffer --cycles 1 --on-s 30 --off-s 1
```

预期：在 30 秒 `ON` 期间可观察到持续 FMCW 波形/频谱。`--reuse-buffer` 用于避免重复创建 DMA 缓冲区造成的启动不稳定。

## 3. 逐层隔离 RX 影响

```powershell
python scripts\diagnose_e310_tx.py --phase fmcw-rx-configured --tx-channel 0 --tx-port B --reuse-buffer --cycles 1 --on-s 15 --off-s 1
python scripts\diagnose_e310_tx.py --phase fmcw-rx-read --tx-channel 0 --tx-port B --reuse-buffer --cycles 1 --on-s 15 --off-s 1
```

若第 2 步成功、第 3 步失败，问题更可能在 RX 配置、DMA 竞争或 RX 物理链路，而不是 TX 波形生成。

## 4. 雷达同步采样与失败 IQ 保存

稳定 20 MHz：

```powershell
python scripts\diagnose_fmcw_sync.py --profile stable-20 --frames 5 --save-captures failures --output-dir artifacts\diagnose-stable-20
```

28 MHz 实验档：

```powershell
python scripts\diagnose_fmcw_sync.py --profile experimental-28 --frames 5 --save-captures failures --output-dir artifacts\diagnose-28
```

40 MHz 验证档（仅在 E310 IIO 回读确认 60 MSPS/40 MHz 后）：

```powershell
python scripts\diagnose_fmcw_sync.py --profile validate-40 --frames 5 --save-captures failures --output-dir artifacts\diagnose-40
```

只报告每帧的同步结果、相关性、RMS/峰值和输出目录；不要把完整 IQ 内容粘贴到对话。

## 5. 视频链路基础探测

```powershell
python scripts\e310_rf_loopback.py --payload-pattern message --payload-bytes 8 --tx-port B --rx-port B_BALANCED --tx-settle-sec 1 --rx-discard-buffers 0
```

预期：输出 `rx_rms_dbfs`、IQ 文件和频谱/星座图。该命令仅用于基础链路观测；视频成功与否以通信测试页的成功/失败块数、吞吐和质量分为准。

## 6. 现场记录最小格式

每轮复测仅保存：

```text
时间：
预设：
角反距离/喇叭状态：
命令：
示波器或 GUI 结果：
关键数值（同步相关性 / RX RMS / 通信质量）：
日志或 artifact 路径：
```

