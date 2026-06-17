# remotevideo / videotool 1.1 软件分析

## 结论

`videotool 1.1` 的主程序名是 `remotevideo`，它不是普通的视频剪辑工具，而是一个用于联调的 Windows WPF 桌面工具。它的主要作用是：

1. 从本地选择一个 MP4 视频文件。
2. 使用 OpenCV/FFmpeg 读取视频帧。
3. 将每帧编码为 WebP 图片，并切成多个 UDP 小包发送到远端。
4. 本地监听一个 UDP 端口，接收远端发回的视频包或雷达目标数据包。
5. 在界面上显示本地发送视频、远端回传视频，以及解析出的雷达目标距离/速度。
6. 根据界面参数生成雷达/波束控制命令，通过 UDP 发给远端设备或仿真端。

源码依据主要来自反编译后的：

- `remotevideo/MainWindow.cs`
- `remotevideo.Views/SettingDialog.cs`
- `BeamControlGenerator.cs`
- `remotevideo.Properties/Settings.cs`

## 程序结构

### 主窗口

主逻辑在 `remotevideo/MainWindow.cs`。

关键方法：

| 方法 | 作用 |
|---|---|
| `InitUdpSocket()` | 创建 UDP socket，绑定本地监听端口，启动接收线程 |
| `CreateRemoteEndpoint()` | 根据设置里的远端 IP 和端口创建发送目标 |
| `OnStartSending_Click()` | 点击开始发送，启动视频读取线程和编码发送线程 |
| `SendVideoProc()` | 读取 MP4 视频帧，并显示在本地界面 |
| `EncodeAndSendProc()` | 从队列取出帧，编码并发送；也负责发送雷达控制命令 |
| `SendVideoFrame()` | 将单帧编码成 WebP，并拆成 UDP 包 |
| `ReceiveVideo()` | UDP 接收循环，按包类型分发到视频解析或雷达解析 |
| `ParsePacket()` | 从 UDP 数据流中按同步头和长度字段拆出完整包 |
| `ParseVideoPacket()` | 重组多包视频帧并显示 |
| `ParseRadarPacket()` | 解析目标距离/速度并显示 |
| `OnUpdateRadarParam_Click()` | 从设置读取雷达参数，生成并触发发送控制命令 |
| `sendRadarCommand()` | 拼装雷达控制 UDP 包并发送 |

### 参数设置窗口

设置窗口在 `remotevideo.Views/SettingDialog.cs`。

它负责读写这些配置：

- 模式 `Mode`
- 通信模式 `CommMode`
- MP4 文件路径 `VideoFilePath`
- 远端 IP `RemoteEndAddr`
- 远端端口 `RemoteEndPort`
- 本地监听端口 `LocalRecvPort`
- 雷达信号、带宽、时宽、中心频率等参数
- 波束控制相关的 TX/RX 起始行、长度、扫描数组 `G`

点击确定后调用 `Settings.Default.Save()` 保存到用户配置。

### 波束控制生成器

`BeamControlGenerator.cs` 负责生成波束控制命令字节流。

输入参数：

| 参数 | 含义 |
|---|---|
| `s1` | TX 起始行 |
| `l1` | TX 行长度 |
| `s2` | RX 起始行 |
| `l2` | RX 行长度 |
| `M` | 扫描/控制模式相关参数 |
| `azimuthArray` | 方位数组，也就是设置中的 `G` |

生成逻辑：

```text
result = [M, N]

对 G 中每个 gValue:
  txStartRow = TXS1 + TXL1 * (gValue - 1)
  rxStartRow = RXS2 + RXL2 * (gValue - 1)
  生成一条 TX 命令
  生成一条 RX 命令
```

单条 TX/RX 命令格式：

```text
A5 5A 07 30 [startRow 3字节大端] [rowCount 3字节大端] [checksum]
```

其中：

- `A5 5A` 是命令头。
- `07` 是 payload 长度。
- `30` 是命令码。
- `startRow` 和 `rowCount` 都用 3 字节大端编码。
- `checksum` 是 payload 字节求和后取低 8 位。

## 默认配置

默认配置来自 `remotevideo.Properties/Settings.cs` 和 `remotevideo.dll.config`。

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `ProtocolVersion` | `1` | 协议版本 |
| `Mode` | `1` | 工作模式 |
| `CommMode` | `1` | 通信模式 |
| `RadarSignal` | `0` | 雷达信号类型 |
| `RadarBandWidth` | `100` | 雷达带宽 |
| `RadarTimeWidth` | `100` | 雷达时宽 |
| `SendInterval` | `50` | 发送间隔配置，不过实际视频发送主要按视频 FPS 睡眠 |
| `VideoFilePath` | 空 | MP4 文件路径 |
| `RemoteEndAddr` | `127.0.0.1` | 远端 IP |
| `RemoteEndPort` | `8099` | 远端 UDP 端口 |
| `LocalRecvPort` | `8098` | 本地 UDP 监听端口 |
| `Nd` | `0` | 雷达参数 |
| `Nr` | `0` | 雷达参数 |
| `Fc` | `73` | 雷达中心频率/频点参数 |
| `RadarReturnDataMode` | `0` | 雷达回传数据模式 |
| `M` | `0` | 波束控制参数 |
| `N` | `11` | 方位数量，实际由 `G` 的数量决定 |
| `TXS1` | `1685` | TX 起始行 |
| `TXL1` | `195` | TX 行长度 |
| `RXS2` | `40685` | RX 起始行 |
| `RXL2` | `195` | RX 行长度 |
| `G` | `1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29` | 方位数组 |
| `Version` | `1.1` | 软件版本 |

## 数据交互方式

### 通信模型

软件只使用 UDP 通信。

默认情况下：

```text
本机监听: 0.0.0.0:8098
远端发送: 127.0.0.1:8099
```

启动时 `InitUdpSocket()` 会：

1. 创建 `UdpClient`。
2. 绑定 `LocalRecvPort`。
3. 启动后台接收线程 `ReceiveVideo()`。

发送数据时 `CreateRemoteEndpoint()` 会：

1. 读取 `RemoteEndAddr`。
2. 读取 `RemoteEndPort`。
3. 创建 `IPEndPoint`。

### 包类型

根据代码中 `packetData[4]` 的使用，可以推断第 5 个字节是包类型字段。

| `packetData[4]` | 含义 |
|---:|---|
| `0` | 视频包 |
| `1` | 雷达目标数据包 |
| `2` | 雷达/设备控制命令包 |

接收端逻辑中：

```text
如果 packetData[4] == 1:
  ParseRadarPacket()
否则:
  ParseVideoPacket()
```

也就是说，本软件接收时只明确区分雷达回传包 `1` 和非雷达包。非雷达包会被当成视频包处理。

## 视频发送流程

点击“开始发送”后：

```text
OnStartSending_Click()
  -> CreateRemoteEndpoint()
  -> 启动 SendVideoProc 线程
  -> 启动 EncodeAndSendProc 线程
```

### 读取视频

`SendVideoProc()` 使用：

```csharp
capture = new VideoCapture(m_filepath);
```

然后循环读取每一帧：

```text
读取 Mat frame
复制一份放入 m_encodeQueue
把当前帧显示到 videoDisplay
按视频 FPS sleep
```

如果读到文件结尾，会把视频位置设回第 0 帧，实现循环播放：

```text
capture.Set(VideoCaptureProperties.PosFrames, 0)
```

### 编码和切包

`EncodeAndSendProc()` 从队列取出帧后调用 `SendVideoFrame()`。

`SendVideoFrame()` 会把图像编码成 WebP：

```csharp
Cv2.ImEncode(".webp", mat, out byte[] imageBytes, new ImageEncodingParam(ImwriteFlags.WebPQuality, 30));
```

然后按 1000 字节切包：

```text
MaxPacketSize = 1000
numOfPackets = ceil(imageBytes.Length / 1000)
```

每个视频 UDP 包大致格式：

```text
offset  size  含义
0       4     同步头: 01 DC EF 18
4       1     包类型: 00 表示视频
5       1     协议版本
6       9     保留字段，发送时填 0
15      2     payload 长度字段
17      1     padding 长度
18      1     当前帧总包数
19      1     当前包序号
20      N     WebP 图片分片数据
20+N    P     padding 0
```

注意：代码使用 `BitConverter` 解析长度字段，在 Windows/.NET 上实际是小端序。`ChoiByteBuffer` 写 `short` 时也需要和接收端保持一致。

### 视频接收和重组

接收端 `ReceiveVideo()` 收到 UDP 数据后调用 `ParsePacket()` 提取完整包。

对于视频包，`ParseVideoPacket()` 会读取：

```text
offset 15: payload 长度
offset 17: padding 长度
offset 18: 当前帧总包数
offset 19: 当前包序号
offset 20: 分片图片数据
```

然后用字典 `m_packetBuffer[packetIndex]` 暂存分片。

当收齐一帧所有分片后：

```text
按包序号 0..N-1 拼接完整 WebP 图片
清空缓存
调用 DisplayImage() 显示到 recvVideoDisplay
```

## 雷达目标数据接收

当接收包的 `packetData[4] == 1` 时，软件认为这是雷达目标数据包。

`ParseRadarPacket()` 从固定偏移解析 3 个目标：

```text
offset 17-18: 目标1距离 int16
offset 19-20: 目标1速度 int16
offset 21-22: 目标2距离 int16
offset 23-24: 目标2速度 int16
offset 25-26: 目标3距离 int16
offset 27-28: 目标3速度 int16
```

解析结果显示到 `tbRadarData` 文本框顶部。

该软件没有看到复杂的雷达点云解析、FFT、成像或目标检测算法。它只是在接收端按固定字段解析远端已经算好的目标距离和速度。

## 雷达控制命令发送

点击“启动设备/更新参数”相关按钮后会进入：

```text
OnUpdateRadarParam_Click()
  -> 从 Settings 读取参数
  -> BeamControlGenerator.GenerateBeamControlCommands(...)
  -> 设置 m_sendRadarCmd = true
```

真正发送发生在 `EncodeAndSendProc()` 线程中：

```text
如果 m_sendRadarCmd == true:
  sendRadarCommand(m_bctlvalues)
```

### 控制命令包

`sendRadarCommand()` 发送的是包类型 `2`。

控制命令包大致格式：

```text
offset  size  含义
0       4     同步头: 01 DC EF 18
4       1     包类型: 02 表示控制命令
5       1     协议版本
6       1     deviceStarted，设备启动标志
7       1     needUpdateParam，参数更新标志
8       1     Mode
9       1     CommMode
10      1     RadarSignal
11      1     RadarBandWidth
12      2     RadarTimeWidth
14      2     Nd
16      2     Nr
18      2     Fc
20      1     RadarReturnDataMode
21      2     M
23      2     N
25      2     波束控制字节长度
27      1     padding 长度
28      N     波束控制字节
28+N    P     padding 0
```

这里的“波束控制字节”来自 `BeamControlGenerator`。

### 控制字节生成示例

默认参数：

```text
TXS1 = 1685
TXL1 = 195
RXS2 = 40685
RXL2 = 195
G = 1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29
```

对 `G=1`：

```text
txStartRow = 1685 + 195 * (1 - 1) = 1685
rxStartRow = 40685 + 195 * (1 - 1) = 40685
```

对 `G=2`：

```text
txStartRow = 1685 + 195 * (2 - 1) = 1880
rxStartRow = 40685 + 195 * (2 - 1) = 40880
```

每个方位生成 2 条命令：一条 TX，一条 RX。

## 硬编码测试发送

`OnClickSendRadarData()` 里还有一个按钮事件，会发送一个固定内容的数据包。

它的包头同样是：

```text
01 DC EF 18
```

包类型字段为 `2`，后面带有固定短整数：

```text
12, 17, 19, 21, 23, 25, 27
```

这更像是开发调试时留下的测试发送按钮，用来向远端发送一帧固定数据。它不是主要的视频发送流程，也不是主要的雷达参数更新流程。

## 协议同步和拆包机制

`ParsePacket()` 用同步头查找包起点：

```text
01 DC EF 18
```

处理逻辑：

1. 将新收到的 UDP 数据追加到 `m_recvBuffer`。
2. 在缓冲区中查找同步头。
3. 如果找不到，只保留最后 3 个字节，防止同步头跨包。
4. 找到同步头后，读取 offset 15 的长度字段。
5. 如果缓冲区还不够一个完整包，就继续等待。
6. 如果够了，就切出完整包并把剩余数据留在缓冲区。

这说明协议本身允许“一个 UDP receive 中有半包或多包”的情况，不过实际 UDP 一般按 datagram 收取。这里的缓冲逻辑更像是从串口/TCP 思路移植过来的，增加了容错。

## 线程模型

软件至少使用 3 个后台线程：

| 线程 | 方法 | 作用 |
|---|---|---|
| 接收线程 | `ReceiveVideo()` | 阻塞等待 UDP 数据，解析视频或雷达包 |
| 视频读取线程 | `SendVideoProc()` | 从 MP4 读取帧，显示本地画面，放入编码队列 |
| 编码发送线程 | `EncodeAndSendProc()` | 编码 WebP、拆包、发送；也发送雷达控制命令 |

UI 更新通过 WPF Dispatcher：

```text
Dispatcher.Invoke(...)
```

## 依赖库作用

| 依赖 | 作用 |
|---|---|
| `OpenCvSharp` | 读取视频、编码图片 |
| `OpenCvSharp.WpfExtensions` | 将 OpenCV Mat 转成 WPF 可显示的 BitmapSource |
| `opencv_videoio_ffmpeg4100_64.dll` | FFmpeg 视频读写后端 |
| `OpenCvSharpExtern.dll` | OpenCvSharp native 运行库 |
| `ChoiByteBuffer` | 辅助拼接字节协议包 |
| `HandyControl` | WPF 界面控件/主题 |
| `Catel.Core` | 日志功能 |

## 风险和限制

1. UDP 不保证可靠传输  
   视频帧拆成多个包后，只要丢一个包，该帧就无法正确重组。

2. 视频分片总数和序号是单字节  
   `numOfPackets` 和 `packetIndex` 都是 `byte`，理论最大 255 包。每包 1000 字节，单帧 WebP 数据最好不要超过约 255 KB。

3. 视频重组没有帧 ID  
   包里只有“当前帧总包数”和“包序号”，没有明确 frameId。如果网络乱序、丢包或前后帧交错，可能把不同帧的分片混在一起。

4. 雷达目标解析是固定偏移  
   只解析 3 个目标，每个目标距离和速度都是 `Int16`。没有看到可变目标数量或复杂结构。

5. 文本编码有乱码  
   反编译出来的中文字符串显示为乱码，例如“版本”“监听端口”“发送参数配置错误”等。原程序运行资源里可能正常，但源码字符串已经需要手动修复。

6. BAML 未还原为原始 XAML  
   当前工程能重新编译运行，但界面文件还是 `.baml`，不是可编辑的 `.xaml`。

## 总体判断

这个软件很可能用于雷达/视频链路联调：

- PC 端把 MP4 视频模拟为实时视频流，通过 UDP 发给设备或另一个程序。
- 设备或仿真端通过 UDP 回传视频/雷达目标结果。
- PC 端显示回传画面和目标距离、速度。
- PC 端可以配置雷达参数，并生成 TX/RX 波束控制命令发送给远端。

从代码看，它更像实验室或项目内部调试工具，而不是完整产品级软件。

