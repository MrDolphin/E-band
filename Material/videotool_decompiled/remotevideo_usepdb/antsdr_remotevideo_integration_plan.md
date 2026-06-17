# 使用 remotevideo 作为 ANTSDR/FPGA 上位机的集成方案

## 目标

希望使用 `remotevideo.exe` 作为 PC 上位机，通过以太网 UDP 与 ANTSDR/FPGA 交互，实现：

1. 上位机下发工作指令。
2. 上位机下发雷达/通信/通感工作参数。
3. 上位机下发波控指令。
4. 上位机向 FPGA/ANTSDR 发送通信波形或波形数据。
5. 上位机接收 FPGA/ANTSDR 回传的雷达目标数据、2DFFT 数据或通信波形/IQ 数据。

## 参考资料

- `Material/FPGA与上位机数据交互说明_以太网版本1.5.2.docx`
- `Material/ANTSDR_E200_R1.0/开发手册`
- `Material/videotool 1.1/videotool 1.1/remotevideo.exe`
- 反编译工程：`Material/videotool_decompiled/remotevideo_usepdb`

## 结论

`remotevideo.exe` 的现有代码已经具备以下可复用能力：

- WPF 上位机界面。
- UDP 发送与接收。
- 本地端口监听。
- 远端 IP/端口配置。
- 协议包拼装。
- 协议包同步头解析。
- 雷达目标距离/速度解析。
- 波控指令生成。

但它不能直接作为 ANTSDR 波形上位机使用，原因是：

- 现有“通信数据”实际是 WebP 视频图片分片。
- 接收端把非雷达包当成图片重组显示。
- 没有 IQ 波形文件读取、生成、解析、保存逻辑。
- 没有按照文档完整实现“通信数据内容大小 + 通信内容”。
- 发送控制包的 `packetData[4]` 使用了 `0x02`，而文档中数据类型字段只说明了 `0x01` 雷达数据和 `0x00` 通信数据，需要和 FPGA 固件确认。

因此建议：保留 `remotevideo` 的 UI、UDP、设置和波控生成代码，重写“视频发送/接收”为“波形数据发送/接收”。

## 网络连接方式

### ANTSDR 开发手册中的普通连接方式

ANTSDR 开箱检测手册给出的示例是：

```text
ANTSDR 网口 IP: 192.168.1.10
PC 网口 IP:    192.168.1.100
子网掩码:      255.255.255.0
```

这个用于普通 ANTSDR 网口测试或 SDRSharp/libiio 场景。

### FPGA 交互说明要求的连接方式

`FPGA与上位机数据交互说明_以太网版本1.5.2.docx` 明确规定：

```text
计算机 IP: 192.168.0.3
板卡 IP:   192.168.0.2
端口号:    8080
协议类型:  UDP
网口速率:  1000 Mbps
```

如果当前 FPGA 固件就是按这份文档实现的，应以该 IP/端口为准。

### 上位机配置

在 Windows 网卡中设置：

```text
IP 地址: 192.168.0.3
子网掩码: 255.255.255.0
网关: 可留空，或按实验网络设置
```

在上位机软件中设置：

```text
RemoteEndAddr = 192.168.0.2
RemoteEndPort = 8080
LocalRecvPort = 8080
```

注意：如果 PC 上已有程序占用 UDP 8080，本软件会绑定失败。此时要关闭占用程序，或修改 FPGA/PC 两端端口。

## UDP 总体交互

### PC 到 FPGA

```text
PC remotevideo
  -> UDP 目的 IP 192.168.0.2
  -> UDP 目的端口 8080
  -> 下发控制参数、波控指令、通信波形数据
```

### FPGA 到 PC

```text
FPGA/ANTSDR
  -> UDP 目的 IP 192.168.0.3
  -> UDP 目的端口 8080
  -> 回传雷达目标数据、雷达 2DFFT 数据、通信/IQ 数据
```

## 公共包头

文档写的是：

```text
数据帧头固定为 0x18EFDC01
```

因为数据按小端字节传输，实际 UDP payload 中前 4 个字节是：

```text
01 DC EF 18
```

这与 `remotevideo` 代码中使用的同步头一致。

公共接收包格式：

```text
offset  size  含义
0       4     帧头: 01 DC EF 18
4       1     数据类型
5       1     软件版本号
6~14    9     预留
15~16   2     数据内容大小
17~     N     数据内容
```

## FPGA 到 PC 的数据

### 雷达数据包

文档定义：

```text
数据类型 0x01: 雷达数据
```

载荷格式：

```text
offset  size  含义
0       4     01 DC EF 18
4       1     0x01
5       1     软件版本号
6~14    9     预留
15~16   2     数据内容大小
17~18   2     雷达目标1距离，int16
19~20   2     雷达目标1速度，int16
21~22   2     雷达目标2距离，int16
23~24   2     雷达目标2速度，int16
25~26   2     雷达目标3距离，int16
27~28   2     雷达目标3速度，int16
29~     N     雷达数据，例如 2DFFT 数据
```

`remotevideo` 当前已经能解析 offset 17~28 的 3 个目标距离/速度。

但它目前忽略 offset 29 之后的数据。如果 FPGA 要回传 2DFFT 数据，需要新增：

- 2DFFT 数据长度检查。
- 2DFFT 数据保存为 `.bin`、`.csv` 或自定义文件。
- 2DFFT 数据显示，例如热力图。

### 通信数据包 / 波形回传包

文档定义：

```text
数据类型 0x00: 通信数据
```

载荷格式：

```text
offset  size  含义
0       4     01 DC EF 18
4       1     0x00
5       1     软件版本号
6~14    9     预留
15~16   2     通信数据内容大小
17~     N     通信数据内容
```

这里的“通信数据内容”需要和 FPGA 约定具体格式，例如：

```text
I/Q 交织 int16 小端:
I0_lo I0_hi Q0_lo Q0_hi I1_lo I1_hi Q1_lo Q1_hi ...
```

或者：

```text
float32 IQ:
I0_float Q0_float I1_float Q1_float ...
```

当前 `remotevideo` 不支持该格式。它收到 `0x00` 后会尝试按 WebP 图片分片解析，因此必须修改。

## PC 到 FPGA 的控制/参数包

文档定义 PC 下发包包含：

```text
offset  size  含义
0       4     01 DC EF 18
4       1     数据类型
5       1     软件版本号
6       1     开始/停止，1 开始工作，0 停止工作
7       1     雷达参数配置使能，bit0 由 0 变 1 时更新雷达参数
8       1     工作模式，0 雷达，1 通信，2 通感
9       1     通信方式，1 为 16QAM，0 为 QPSK
10      1     雷达模式信号，0 模拟模式，1 输出 Chirp 信号
11      1     雷达 Chirp 信号带宽，单位 MHz
12~13   2     雷达 Chirp 信号时宽，单位 1 us
后续     -     Nd、Nr、Fc、回传模式、波控、通信数据
```

文档表格从 `12` 之后的字段排版有歧义。`remotevideo` 当前代码实际使用的是：

```text
offset  size  含义
0       4     01 DC EF 18
4       1     当前代码写 0x02
5       1     协议版本
6       1     deviceStarted
7       1     needUpdateParam
8       1     Mode
9       1     CommMode
10      1     RadarSignal
11      1     RadarBandWidth
12~13   2     RadarTimeWidth
14~15   2     Nd
16~17   2     Nr
18~19   2     Fc
20      1     RadarReturnDataMode
21~22   2     M
23~24   2     N
25~26   2     波控指令长度
27      1     padding 长度
28~     N     波控指令
```

这个格式和文档的总体意图一致，但存在一个关键差异：

```text
remotevideo 使用 packet type = 0x02
文档只说明 packet type = 0x01 雷达数据，0x00 通信数据
```

必须确认 FPGA 固件到底按哪个值判断控制包：

- 如果 FPGA 固件已经适配 `remotevideo`，可以保留 `0x02`。
- 如果 FPGA 固件严格按文档，应把控制包类型改为：
  - 雷达/通感控制使用 `0x01`
  - 通信/波形数据使用 `0x00`

## 波控指令

文档要求每个方位生成 2 条 11 字节指令：

```text
第 2n-1 条: TX 指令
第 2n   条: RX 指令
```

每条 11 字节：

```text
A5 5A 07 30 [startRow 3字节大端] [rowCount 3字节大端] [checksum]
```

`remotevideo` 当前 `BeamControlGenerator` 已经按该规则实现：

```text
txStartRow = TXS1 + TXL1 * (G[n] - 1)
rxStartRow = RXS2 + RXL2 * (G[n] - 1)
```

默认参数：

```text
TXS1 = 1685
TXL1 = 195
RXS2 = 40685
RXL2 = 195
G = 1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29
```

因此波控生成部分可以直接复用。

## 波形发送应该怎么做

当前 `remotevideo` 的“视频发送”流程是：

```text
MP4 -> OpenCV Mat -> WebP -> 1000 字节分片 -> UDP 发送
```

要改成 ANTSDR/FPGA 波形发送，应替换成：

```text
波形文件或软件生成波形
  -> 按 FPGA 约定编码为 IQ 数据
  -> 按 UDP MTU 分片
  -> 组包
  -> 发往 192.168.0.2:8080
```

建议新增或替换这些方法：

| 当前方法 | 建议替换为 | 说明 |
|---|---|---|
| `SendVideoProc()` | `LoadOrGenerateWaveformProc()` | 读取或生成 IQ 波形 |
| `SendVideoFrame()` | `SendWaveformChunk()` | 发送通信波形分片 |
| `ParseVideoPacket()` | `ParseCommWaveformPacket()` | 接收通信/IQ 回传 |
| `videoDisplay` / `recvVideoDisplay` | 波形图/频谱图/文件保存 | 不再显示图片 |

## 推荐的波形数据格式

如果 FPGA 端还没有固定通信内容格式，建议采用简单明确的格式：

```text
通信数据内容:
uint32 frameId
uint16 chunkIndex
uint16 chunkCount
uint16 sampleFormat
uint16 sampleCount
byte[] iqPayload
```

其中 `iqPayload` 推荐：

```text
int16 IQ 小端交织:
I0, Q0, I1, Q1, ...
```

每个 IQ 样点占 4 字节。

示例：

```text
I0 int16 little-endian
Q0 int16 little-endian
I1 int16 little-endian
Q1 int16 little-endian
```

原因：

- FPGA 端容易解析。
- PC 端容易生成和保存。
- 比 float32 节省带宽。
- 适合 UDP 分片。

如果 FPGA 已经定义了通信数据格式，应以 FPGA 为准，不要另行设计。

## 推荐的最小改造步骤

### 第 1 步：先验证网络

1. PC 网卡设置为 `192.168.0.3/255.255.255.0`。
2. ANTSDR/FPGA 设置或固化为 `192.168.0.2`。
3. 确认 PC 能 ping 通：

```powershell
ping 192.168.0.2
```

如果 FPGA 不响应 ICMP，也要用 Wireshark 看是否有 ARP/UDP。

### 第 2 步：修改 remotevideo 配置

设置：

```text
RemoteEndAddr = 192.168.0.2
RemoteEndPort = 8080
LocalRecvPort = 8080
```

原始默认值 `127.0.0.1:8099` 只适合本机回环测试。

### 第 3 步：先只发控制包

先不要发波形。

只测试：

```text
开始/停止
工作模式
通信方式
雷达参数
波控指令
```

在 FPGA 端用 ILA/Wireshark/调试寄存器确认字段是否正确到达。

### 第 4 步：确认 packet type

这是最重要的兼容点。

需要确认 FPGA 判断的是：

```text
0x02: 控制包
```

还是文档中的：

```text
0x01: 雷达数据/雷达相关
0x00: 通信数据
```

如果 FPGA 严格按文档，必须修改 `sendRadarCommand()` 中的：

```csharp
buffer.Put(2, escapeIfExist: true);
```

改为 FPGA 期望的数据类型。

### 第 5 步：实现波形发送

新增通信波形发送逻辑：

```text
读取 IQ 文件 / 生成 IQ
按块切分
每块封装为 UDP 包
发送到 FPGA
```

不要复用 WebP 视频格式。

### 第 6 步：实现波形接收

把 `ParseVideoPacket()` 改为：

```text
ParseCommWaveformPacket()
```

根据 FPGA 回传的通信数据格式：

- 重组分片。
- 保存 `.bin` 或 `.iq` 文件。
- 可选显示时域波形、星座图、频谱图。

### 第 7 步：实现雷达 2DFFT 接收

当前只显示 3 个目标的距离/速度。

如果需要 2DFFT：

```text
offset 29 之后的数据
  -> 按 FPGA 定义转成矩阵
  -> 保存文件
  -> 绘制热力图
```

## 数据传输建议

### 控制命令

使用单包 UDP。

原因：

- 控制包很小。
- 波控 N=11 时，波控数据为 `2 * 11 * 11 = 242` 字节。
- 加上控制头也远小于 MTU。

### 波形数据

必须分片。

建议每个 UDP payload 不超过：

```text
1200 字节
```

原因：

- 以太网 MTU 通常为 1500。
- IP/UDP 头会占用 28 字节。
- 留余量可以避免 IP 分片。

### 回传数据

雷达目标数据可以单包。

2DFFT 或 IQ 数据需要分片，并且建议加入：

```text
frameId
chunkIndex
chunkCount
payloadLength
checksum 或 CRC
```

否则 UDP 丢包/乱序时，上位机很难判断数据是否完整。

## 是否可以继续使用原始 remotevideo.exe

只能部分使用。

可以直接使用的部分：

- 设置远端 IP/端口。
- 本地 UDP 监听。
- 雷达目标 1/2/3 距离速度显示。
- 波控参数输入。
- 波控指令生成。

不能直接满足的部分：

- 发送波形。
- 接收 IQ 波形。
- 显示频谱、星座图、2DFFT。
- 按 FPGA 文档完整封装通信数据内容。

建议使用反编译并已修复可编译的工程继续开发：

```text
Material/videotool_decompiled/remotevideo_usepdb
```

## 推荐最终方案

不要把 `remotevideo.exe` 当成不可修改黑盒使用。

推荐做法：

1. 以 `remotevideo_usepdb` 为源码工程。
2. 保留 UI、设置、UDP 通信、波控生成。
3. 删除或旁路视频 MP4/WebP 发送逻辑。
4. 新增 `WaveformSender`：
   - 生成 QPSK/16QAM/IQ 数据；
   - 或读取 `.bin/.iq` 文件；
   - 按协议分片发送。
5. 新增 `WaveformReceiver`：
   - 接收 FPGA 通信数据；
   - 重组并保存；
   - 显示时域/频域/星座图。
6. 扩展 `RadarDataReceiver`：
   - 保留目标距离/速度；
   - 增加 2DFFT 数据保存和显示。
7. 用 Wireshark 对照协议逐字段验证。

## 最小联调顺序

推荐按这个顺序做，避免一上来把问题混在一起：

1. PC 与 FPGA 网口互通。
2. PC 发送一个开始工作控制包。
3. FPGA 确认收到包头和字段。
4. PC 发送波控指令。
5. FPGA 确认波控字节长度和每条 11 字节指令。
6. FPGA 回传一个固定雷达目标包。
7. PC 显示目标距离/速度。
8. PC 发送一小段固定 IQ 波形。
9. FPGA 确认接收样点正确。
10. FPGA 回传一小段固定 IQ 波形。
11. PC 保存并画图。
12. 再接入真实 AD9361 收发链路。

