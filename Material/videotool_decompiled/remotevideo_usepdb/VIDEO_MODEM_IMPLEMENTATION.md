# E310 无线视频通信实现记录

## 当前阶段

阶段一已经完成 PC 内部软件闭环：

```text
MP4
→ OpenCV读取视频帧
→ WebP压缩
→ 无线视频分片
→ CRC32
→ QPSK调制
→ 前导同步和相位估计
→ QPSK解调
→ CRC32检查
→ 视频分片重组
→ 接收视频显示
```

该阶段不经过 E310、TRX、相控阵和空间链路，目的是先证明上位机通信协议和视频恢复逻辑正确。

## 使用方法

运行：

```text
bin/video-modem-stage1/remotevideo.exe
```

设置：

```text
通信方式：8
视频文件：选择一个MP4文件
```

点击“发送视频”。

界面预期：

- 上方“发送视频”显示原始视频。
- 下方“接收视频”显示经过 QPSK 软件闭环恢复的视频。
- 右侧显示视频帧号、WebP大小、分片数、解调成功数、前导相关值、CRC状态和累计失败数。

正常情况下：

```text
成功解调 = 本帧分片
前导相关 ≈ 1.0000
累计失败 = 0
CRC = 通过
```

## 无线视频帧

```text
偏移    长度    内容
0       4       同步字 0x91D35555
4       1       协议版本
5       1       数据类型
6       4       视频帧号
10      2       分片序号
12      2       总分片数
14      2       有效载荷长度
16      4       时间戳
20      N       WebP数据
20+N    4       CRC32
```

单个分片有效载荷当前为 700 字节，上限为 1024 字节。

## QPSK物理层

当前第一版参数：

```text
调制：QPSK Gray映射
过采样：4 samples/symbol
前导：16字节，64个QPSK符号
帧长字段：32bit小端
同步：前导相关搜索
相位恢复：前导相关相位估计
脉冲成形：暂未加入
FEC：暂未加入
```

## 自动测试

执行：

```powershell
dotnet run -c Release --project video_modem_selftest\video_modem_selftest.csproj
```

测试内容：

1. 无线帧序列化与CRC。
2. 视频乱序分片重组。
3. 带采样偏移和载波相位偏移的QPSK恢复。
4. 带高斯噪声的QPSK恢复。

## 阶段二：E310硬件链路

阶段二已经实现：

```text
PC生成连续QPSK IQ
→ UDP发送到E310
→ E310非cyclic连续TX
→ TX1/TRX/相控阵/空间/TRX/RX1
→ E310连续RX
→ UDP回传PC
→ 上位机同步解调和视频恢复
```

阶段二文件：

```text
上位机：bin/video-modem-stage2/remotevideo.exe
E310：e310_udp_receiver/video_modem_bridge
```

参数：

```text
LO：200 MHz
采样率：3.84 MSPS
RF带宽：3 MHz
符号率：960 ksym/s
调制：QPSK
过采样：4 samples/symbol
TX衰减：-30.5 dB
```

### 部署E310程序

```powershell
scp -O `
  "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\video_modem_bridge" `
  root@192.168.1.10:/mnt/sdcard/
```

首次测试先停止FMCW后台程序：

```bash
killall udp_receiver_fmcw 2>/dev/null
killall video_modem_bridge 2>/dev/null
chmod +x /mnt/sdcard/video_modem_bridge
/mnt/sdcard/video_modem_bridge
```

正常启动日志：

```text
E310 video modem bridge listening on UDP 8080
LO=200 MHz Fs=3.84 MSPS BW=3 MHz TX1/RX1 non-cyclic QPSK stream
```

不要同时运行 `udp_receiver_fmcw` 和 `video_modem_bridge`，二者都会占用 UDP 8080。

## Stage 2.1：单次播放与接收诊断

修复版上位机：

```text
bin/video-modem-stage2-fix/remotevideo.exe
```

本版本包含：

- MP4 播放到文件末尾后不再循环；
- 最后的视频编码队列排空后，自动向 E310 发送停止命令；
- 右侧面板在尚未解调出视频时也显示 UDP IQ 包数、完整 IQ 帧数、
  不完整帧数、累计样点和 QPSK 前导相关值；
- 接收线程不再因单个无效 UDP 包暂停 50 ms；
- E310 的 RX 帧由 16384 点改为 4096 点，每帧约 12 个 UDP 分片，
  降低单个 UDP 包丢失导致整帧作废的概率。

修改 E310 程序后，需要使用此前已经验证可兼容 GLIBC 2.25 的
Linaro 7.5 工具链重新编译 `e310_udp_receiver/video_modem_bridge.c`，
再替换 SD 卡上的 `video_modem_bridge`。

在 WSL 中执行：

```bash
cd /mnt/d/hp-laptop/E-band/Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
sh build_video_modem_bridge.sh
```

生成文件：

```text
video_modem_bridge_4096
```

部署：

```powershell
scp -O "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\video_modem_bridge_4096" root@192.168.1.10:/mnt/sdcard/video_modem_bridge
```

新版上位机：

```text
bin/video-modem-stage3-sync/remotevideo.exe
```

Stage 3 额外改进：

- UDP 接收与 QPSK 解调改为两个独立线程；
- 解调变慢时不会直接阻塞 UDP `Receive`；
- 右侧增加解调队列深度和解调队列丢帧；
- 每个符号使用 4 个采样积分判决；
- 从前导估计并校正载波频偏；
- 前导门限由 0.80 调整为 0.65，并继续使用无线帧 CRC 防止误判；
- 无有效前导时及时丢弃旧 IQ，避免反复扫描 20 万样点。

## Stage 4：快速同步

实测 4096 样点版本已经将 UDP IQ 不完整帧降为 0，但 Stage 3
仍可能出现解调队列积压。Stage 4 将前导搜索改为粗搜索加局部精搜索，
并增加判决导向载波跟踪。

新版上位机：

```text
bin/video-modem-stage4-fast-sync/remotevideo.exe
```

观察顺序：

```text
1. 不完整/丢失 IQ 帧应继续保持为 0
2. 解调队列应能回落，队列丢帧增长速度应显著下降
3. 最佳前导相关超过 0.65 后，累计 RX 分片应开始增加
4. 恢复视频帧增加后，下方显示接收视频
```

右侧状态判断：

```text
UDP IQ 数据包一直为 0
    E310 没有回传，或监听端口/防火墙错误

UDP IQ 数据包增加，但完整 IQ 帧很少
    PC 侧 UDP 丢包严重

完整 IQ 帧持续增加，但前导相关低于 0.80
    射频链路、频偏、采样同步或 QPSK 波形仍需处理

累计 RX 分片增加
    QPSK 和无线帧 CRC 已成功

恢复视频帧增加
    下方接收视频应同步更新
```

### 上位机操作

```text
通信方式：9
目标地址：192.168.1.10
目标端口：8080
监听端口：8098
视频文件：选择MP4
```

点击“发送视频”。下方“接收视频”显示从 RX1 IQ 中恢复的视频。

右侧预期显示：

```text
累计TX分片
累计RX分片
恢复视频帧
CRC/解调失败
前导相关
```

### 推荐测试顺序

第一步建议：

```text
E310 TX1 → 40～60 dB衰减器 → E310 RX1
```

没有衰减器时不要用短SMA线直接连接TX1和RX1。也可以使用已调试的低功率TRX/空间链路，但问题变量会更多。

正常判定：

```text
累计RX分片持续增加
恢复视频帧持续增加
CRC/解调失败接近0
下方视频连续更新
```

E310日志应周期出现：

```text
video IQ queued
video TX sent
video RX returned
```

### 当前限制

阶段二尚未加入：

1. RRC脉冲成形和匹配滤波。
2. 载波频偏估计与跟踪。
3. 符号定时恢复。
4. Costas环连续相位跟踪。
5. FEC前向纠错。
6. ARQ重传与码率自适应。

直接E310回环最容易成功。经过独立TRX本振和空间链路后，如果RX IQ持续回传但无法恢复分片，下一阶段优先加入频偏估计、符号定时和RRC滤波。
