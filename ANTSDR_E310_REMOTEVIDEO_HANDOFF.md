# ANTSDR E310 remotevideo 项目交接总结

更新时间：2026-06-17

## 1. 当前目标

基于 `remotevideo.exe` 上位机和 ANTSDR E310，实现：

- PC 上位机发送视频；
- 上位机将视频帧编码为 QPSK IQ 数据；
- E310 通过 TX1 发射；
- 信号经过外部 TRX/相控阵/喇叭天线/回传链路；
- E310 RX1 接收 IQ；
- E310 将 RX IQ 通过 UDP 回传 PC；
- 上位机解调、分片重组、CRC 校验，并在“接收视频”区域显示恢复视频。

当前已经实现并验证：回环链路下可以恢复视频帧，接收视频区域已经能显示画面。

## 2. 主要工程路径

源码目录：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb
```

上位机主程序：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo\MainWindow.cs
```

视频无线分片/重组：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo\WirelessVideoFrame.cs
```

QPSK 调制解调：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo\QpskModem.cs
```

E310 端桥接程序目录：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver
```

当前推荐上位机版本：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\bin\video-modem-stage14-adaptive-throughput\remotevideo.exe
```

当前 E310 端程序：

```text
/mnt/sdcard/video_modem_bridge
/tmp/video_modem_bridge
```

日志：

```text
/tmp/video_modem.log
```

## 3. 当前硬件链路

用户实际目标链路：

```text
PC remotevideo
  -> UDP 到 E310
  -> E310 TX1
  -> TRX 上下变频模块
  -> 相控阵雷达
  -> 空间传输
  -> 喇叭天线
  -> TRX 上下变频模块
  -> E310 RX1
  -> UDP RX IQ 回 PC
  -> remotevideo 解调并显示视频
```

前期调试链路：

```text
E310 TX1 -> 线缆/外部链路 -> E310 RX1
```

注意：如果 TX1/RX1 直接用 2.92 mm SMA 线硬连，建议加衰减器。没有衰减器时只能通过降低 TX hardwaregain 和 IQ 幅值缓解，但不能完全替代衰减器。

## 4. 网络与端口

E310 IP：

```text
192.168.1.10
```

PC IP：

```text
192.168.1.200
```

上位机通信设置：

```text
通信方式：9
目标地址：192.168.1.10
目标端口：8080
监听端口：8098
```

E310 端监听：

```text
UDP 8080
```

PC 接收 RX IQ：

```text
UDP 8098
```

## 5. E310 当前射频参数

视频 QPSK 链路主要参数：

```text
LO：200 MHz
采样率：3.84 MSPS
符号率：960 ksym/s
调制：QPSK
每符号采样：4 samples/symbol
TX：TX1
RX：RX1
```

常用查询命令：

```sh
iio_attr -c ad9361-phy -o altvoltage1 frequency
iio_attr -c ad9361-phy -o voltage0 hardwaregain
iio_attr -c ad9361-phy -i voltage0 gain_control_mode
iio_attr -c ad9361-phy -i voltage0 hardwaregain
```

曾用 RX 设置：

```sh
iio_attr -c ad9361-phy -i voltage0 gain_control_mode manual
iio_attr -c ad9361-phy -i voltage0 hardwaregain 30
```

如果 RX 过强或出现削顶，降低 RX 增益。

## 6. E310 自动启动

E310 的 `/root` 是临时文件系统，重启后会丢失。

持久目录：

```text
/mnt/jffs2
/mnt/sdcard
```

当前使用 `/mnt/jffs2/autorun.sh` 自动挂载 SD 卡并启动程序。

典型 `autorun.sh`：

```sh
#!/bin/sh

mkdir -p /mnt/sdcard

if ! grep -q " /mnt/sdcard " /proc/mounts; then
    mount -t vfat /dev/mmcblk0p1 /mnt/sdcard
fi

if [ -f /mnt/sdcard/video_modem_bridge ]; then
    cp /mnt/sdcard/video_modem_bridge /tmp/video_modem_bridge
    chmod +x /tmp/video_modem_bridge
    /tmp/video_modem_bridge >/tmp/video_modem.log 2>&1 &
fi
```

查看进程：

```sh
ps -ef | grep video_modem
```

查看日志：

```sh
tail -f /tmp/video_modem.log
```

## 7. 已完成的软件阶段

### Stage 13

版本：

```text
bin\video-modem-stage13-selective-repair\remotevideo.exe
```

特点：

- 5 轮全量重复发送；
- 加入选择性补发；
- 视频恢复稳定；
- 实测恢复视频帧成功。

代表结果：

```text
TX 分片：1436
选择性补发：11
RX 分片：1282
恢复视频帧：6
CRC/格式失败：23
```

结论：可靠性较好，但冗余偏高，速度较慢。

### Stage 14

版本：

```text
bin\video-modem-stage14-adaptive-throughput\remotevideo.exe
```

特点：

- 全量发送从 5 轮降为 2 轮；
- 最多 4 轮选择性补发；
- 增加发送统计：
  - 发送源帧；
  - 接收确认完整帧；
  - 累计 TX 分片；
  - 其中选择性补发；
  - 累计 RX 分片；
  - 恢复视频帧；
  - CRC/格式失败。

目标：在保持恢复率的同时提高视频刷新速度。

## 8. 当前上位机显示含义

右侧“雷达数据”区域在通信方式 9 下显示 QPSK 视频链路状态。

关键字段：

```text
当前 RX IQ 帧：E310 回传的 IQ 帧编号
UDP IQ 数据包：收到的 UDP IQ 包数量
完整 IQ 帧：完整拼出的 RX IQ 帧数量
QPSK 流缓冲：解调器内部缓存样点数
正常 IQ 相关：正常 IQ 方向的前导相关度
共轭 IQ 相关：共轭 IQ 方向的前导相关度
最佳前导相关：当前最好的 QPSK 前导匹配值
RX RMS：接收信号强度
RX 峰值：接收峰值
I/Q 功率失衡：I 和 Q 幅度差
I/Q 相关系数：I 和 Q 是否异常相关
粗频偏估计：载波频偏估计
削顶样点：是否过载
全零样点：是否出现零样点
累计 TX 分片：上位机发出的无线分片数
其中选择性补发：补发的分片数
发送源帧：尝试发送的视频帧数
接收确认完整帧：发送侧确认接收端已完整恢复的帧数
累计 RX 分片：解调出的有效视频分片
恢复视频帧：已成功重组并显示的视频帧
CRC/格式失败：解调后 CRC 或格式解析失败次数
```

判断链路是否正常：

```text
恢复视频帧 > 0
接收视频窗口有画面
CRC/格式失败增长不要远大于 RX 分片
削顶样点接近 0
I/Q 功率失衡最好小于 1 dB
粗频偏尽量在几百 Hz 内
```

## 9. 诊断文件

上位机会保存诊断文件：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\bin\<stage>\diagnostics
```

常见文件：

```text
rx_capture_YYYYMMDD_HHMMSS.iq
rx_metrics_YYYYMMDD_HHMMSS.csv
rx_capture_YYYYMMDD_HHMMSS.analysis.png
rx_capture_YYYYMMDD_HHMMSS.active.png
```

分析脚本：

```text
D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\analyze_video_iq.py
```

运行示例：

```powershell
python "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\analyze_video_iq.py" `
  "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\bin\video-modem-stage14-adaptive-throughput\diagnostics\rx_capture_xxx.iq"
```

## 10. 诊断图怎么看

### analysis.png

包含全局捕获数据：

1. Time domain  
   看 I/Q 是否有明显削顶、断续、直流偏置。

2. Baseband spectrum  
   看基带频谱是否合理，有无明显频偏、镜像、过强 DC。

3. I/Q constellation  
   看 QPSK 星座点是否清晰。理想情况应该有四个主要星座簇。当前链路中有拖尾和额外簇，说明通道/滤波/同步仍有改善空间。

### active.png

选取 RMS 最强的 4096 样点窗口，用于观察“真正有信号的一小段”。

作用：

- 如果全局图被大量空闲/弱信号稀释，active 图更能看出调制质量；
- 当前 active 图中星座有多个簇和拖尾，说明解调可用，但仍不是很干净；
- 这也是后续优化同步、滤波、均衡的主要依据。

## 11. 最近一次较好结果

Stage 13/14 前后的有效结果：

```text
恢复视频帧已经大于 0
接收视频区域已经显示画面
削顶样点：0%
I/Q imbalance：约 0.5 dB
Coarse QPSK frequency offset：约几十 Hz 到几百 Hz
Peak：约 0.05～0.06
```

这说明：

- RX 没有明显过载；
- 信号强度偏低但可用；
- 频偏已经不是主要问题；
- 当前瓶颈更像是符号同步、滤波、帧边界、星座判决鲁棒性。

## 12. 常用构建命令

进入目录：

```powershell
cd D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb
```

运行自测：

```powershell
dotnet run --project "video_modem_selftest\video_modem_selftest.csproj"
```

Release 构建：

```powershell
dotnet build "remotevideo.csproj" -c Release
```

发布新版本：

```powershell
dotnet publish "remotevideo.csproj" -c Release -o "bin\video-modem-stageXX-name"
```

## 13. 常用 E310 操作

复制程序到 SD 卡：

```powershell
scp -O "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\video_modem_bridge" root@192.168.1.10:/mnt/sdcard/video_modem_bridge
```

重启：

```powershell
ssh root@192.168.1.10 "sync; reboot"
```

如果 SSH 提示 host key 变化：

```powershell
ssh-keygen -R 192.168.1.10
```

登录：

```powershell
ssh root@192.168.1.10
```

查看日志：

```sh
tail -f /tmp/video_modem.log
```

## 14. 当前下一步建议

建议从 Stage 14 继续：

1. 用 `video-modem-stage14-adaptive-throughput\remotevideo.exe` 测试实际链路；
2. 观察：
   - 发送源帧；
   - 接收确认完整帧；
   - 恢复视频帧；
   - CRC/格式失败；
   - 下方接收视频刷新速度。
3. 如果 Stage 14 恢复率下降：
   - 将 `VideoChunkRepeatCount` 从 2 改回 3；
   - 保持 `VideoRepairRoundCount = 4`。
4. 如果恢复率稳定：
   - 继续减少冗余；
   - 或提高视频帧率/降低 JPEG/WebP 压缩；
   - 或增加更强的同步与均衡算法。

## 15. 后续可优化方向

优先级从高到低：

1. 增强 QPSK 同步
   - 更稳定的前导检测；
   - 更好的符号定时恢复；
   - 更好的相位旋转估计。

2. 加入简单均衡
   - 解决星座拖尾、多簇问题；
   - 适应 TRX/相控阵/空间链路造成的幅相畸变。

3. 自适应补发
   - 根据最近几帧恢复率自动选择 2/3/4 轮冗余；
   - 链路好时提速，链路差时保可靠。

4. FEC 前向纠错
   - 比重复发送更高效；
   - 可以考虑 Reed-Solomon 或 LDPC/卷积码。

5. 视频策略
   - 降低单帧分辨率；
   - 控制 WebP/JPEG 质量；
   - 只发送变化区域；
   - 降低帧率，提高可靠性。

## 16. 新窗口接续提示词

可以把下面这段直接发给新窗口：

```text
我们在 D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb 里开发 remotevideo 上位机和 ANTSDR E310 视频链路。

当前推荐版本是 bin\video-modem-stage14-adaptive-throughput\remotevideo.exe。
E310 端运行 /mnt/sdcard/video_modem_bridge，经 /mnt/jffs2/autorun.sh 启动到 /tmp/video_modem_bridge，日志 /tmp/video_modem.log。

通信方式 9，目标 192.168.1.10:8080，监听 8098。E310 参数：LO 200 MHz，Fs 3.84 MSPS，QPSK，960 ksym/s，4 samples/symbol，TX1/RX1。

Stage 13 已经稳定恢复视频；Stage 14 把全量重复从 5 轮降到 2 轮，加 4 轮选择性补发，并增加“发送源帧/接收确认完整帧”统计。当前接收视频区域已经能显示画面。下一步是测试 Stage 14 的恢复率和刷新速度，如果恢复率下降，把 MainWindow.cs 里的 VideoChunkRepeatCount 从 2 改为 3。

请继续优化 QPSK 解调、选择性补发和视频恢复显示。
```
