# E310 libiio 验证步骤

## 目的

本步骤用于验证电脑能通过 libiio 访问 ANTSDR E310 的 FMCOMMS/AD9361 链路。

它验证的是：

- PC 与 E310 网络是否连通；
- E310 上是否运行了 IIO 服务；
- PC 能否通过 `ip:192.168.1.10` 发现 AD9361 设备；
- 后续 E310 端 UDP 接收程序是否有条件把 IQ 数据写入 AD9361 TX。

它不验证：

- `remotevideo` 自定义 UDP 协议；
- 雷达相控阵波控；
- 视频文件调制；
- FPGA 自定义算法。

当前网络配置：

```text
E310 IP:  192.168.1.10
PC IP:    192.168.1.200
Mask:     255.255.255.0
```

这个配置是合理的，二者在同一个 `192.168.1.x` 网段。

## 第 1 步：确认物理连接

1. E310 上电。
2. 网线连接 E310 和电脑。
3. 电脑网卡设置为：

```text
IP 地址:      192.168.1.200
子网掩码:     255.255.255.0
默认网关:     可留空，或 192.168.1.1
DNS:          可留空
```

4. 确认网卡状态为已连接，速率最好是 1.0 Gbps。

## 第 2 步：ping E310

在 Windows PowerShell 或 CMD 中执行：

```powershell
ping 192.168.1.10
```

成功时应看到类似：

```text
来自 192.168.1.10 的回复: 字节=32 时间<1ms TTL=64
```

如果失败：

- 检查电脑是否真的配置到了连接 E310 的那张网卡；
- 检查 E310 是否启动完成；
- 检查网线和网口灯；
- 确认 E310 固件 IP 是否仍为 `192.168.1.10`；
- 如果系统禁用了 ICMP，也可能 ping 不通，但后续 `iio_info` 仍可尝试。

## 第 3 步：准备 Windows 版 libiio 工具

本地资料包中有 Windows 版 libiio 压缩包：

```text
D:\hp-laptop\E-band\Material\ANTSDR_E200_R1.0\SDR_Software\Matlab_Plugin\R2018a_Hardware_Support_Package\R2018a\archives\3p\libiio.instrset_win64_1517897002\libiio-0.14.g17b73d3-Windows.zip
```

虽然路径在 E200 资料包里，但 `libiio` 是通用工具，可用于 E310 的 IIO 验证。

建议解压到：

```text
D:\hp-laptop\E-band\tools\libiio-windows
```

PowerShell 解压示例：

```powershell
New-Item -ItemType Directory -Force "D:\hp-laptop\E-band\tools\libiio-windows"
Expand-Archive "D:\hp-laptop\E-band\Material\ANTSDR_E200_R1.0\SDR_Software\Matlab_Plugin\R2018a_Hardware_Support_Package\R2018a\archives\3p\libiio.instrset_win64_1517897002\libiio-0.14.g17b73d3-Windows.zip" -DestinationPath "D:\hp-laptop\E-band\tools\libiio-windows" -Force
```

解压后查找：

```powershell
Get-ChildItem -Recurse "D:\hp-laptop\E-band\tools\libiio-windows" -Filter "iio_info.exe"
```

如果找到了 `iio_info.exe`，进入它所在目录：

```powershell
cd "iio_info.exe所在目录"
```

## 第 4 步：扫描 IIO 设备

先执行：

```powershell
.\iio_info.exe -s
```

期望输出中能看到类似：

```text
Library version: ...
Compiled with backends: local xml ip usb
Available contexts:
        0: 192.168.1.10 ...
```

如果 `-s` 找不到设备，不代表一定失败。可以直接指定 IP：

```powershell
.\iio_info.exe -u ip:192.168.1.10
```

## 第 5 步：查看 E310 IIO 设备详情

执行：

```powershell
.\iio_info.exe -u ip:192.168.1.10
```

成功时应该看到很多 IIO 设备和通道。重点查找这些关键词：

```text
ad9361-phy
cf-ad9361-lpc
cf-ad9361-dds-core-lpc
voltage0
voltage1
altvoltage0
altvoltage1
sampling_frequency
rf_bandwidth
frequency
hardwaregain
```

一般含义：

| 名称 | 含义 |
|---|---|
| `ad9361-phy` | AD9361 控制设备，用于设置频率、采样率、带宽、增益等 |
| `cf-ad9361-lpc` | RX 接收数据通道 |
| `cf-ad9361-dds-core-lpc` | TX 发送数据通道 |
| `altvoltage*` | 本振/LO 相关通道 |
| `voltage0/1` | I/Q 或 RX/TX 数据通道 |

如果能看到这些，说明 PC 可以通过 libiio 访问 E310 的 AD9361。

## 第 6 步：常见失败原因

### 1. `iio_info.exe` 不是内部或外部命令

原因：没有进入 `iio_info.exe` 所在目录，或未加入 PATH。

处理：

```powershell
cd "iio_info.exe所在目录"
.\iio_info.exe -u ip:192.168.1.10
```

### 2. `Unable to create IIO context`

可能原因：

- E310 没启动完成；
- IP 不通；
- E310 没运行 IIO 服务；
- 当前固件不是 FMCOMMS/libiio 固件；
- Windows 防火墙阻止；
- 网卡不在同一网段。

处理：

```powershell
ping 192.168.1.10
```

然后登录 E310 检查服务。

### 3. 能 ping 通，但 `iio_info` 不通

可能是 E310 上的 IIO daemon 没启动。

通过 SSH 登录 E310：

```powershell
ssh root@192.168.1.10
```

默认账号：

```text
username: root
password: analog
```

登录后检查：

```bash
ps | grep iio
```

如果有 `iiod`，说明 IIO daemon 在运行。

如果没有，尝试：

```bash
iiod &
```

然后 PC 端重新执行：

```powershell
.\iio_info.exe -u ip:192.168.1.10
```

### 4. 设备列表里没有 AD9361

可能原因：

- 固件不是 FMCOMMS/AD9361 固件；
- BOOT.bin/devicetree 与硬件不匹配；
- AD9361 初始化失败；
- E310 启动文件不对。

此时需要先回到 E310 FMCOMMS 固件文档，确认 SD 卡/BOOT 分区/启动文件正确。

## 第 7 步：在 E310 本机验证

如果 E310 固件里自带 `iio_info`，可以直接在 E310 上执行：

```bash
iio_info
```

或：

```bash
iio_info -s
```

这验证的是 E310 本机 IIO 设备，不经过网络。

如果 E310 本机能看到 AD9361，但 PC 端看不到，问题在网络或 `iiod`。

如果 E310 本机也看不到 AD9361，问题在固件/设备树/硬件初始化。

## 第 8 步：和方案 B 的关系

我们后续方案 B 是：

```text
remotevideo.exe
  -> UDP 发包到 192.168.1.10:8080
  -> E310 上的 udp_receiver
  -> udp_receiver 解析 IQ/视频/波控包
  -> 通过 libiio 或 FPGA DMA 写入 AD9361/FPGA
```

所以 libiio 验证通过后，下一步才是写 E310 上的 `udp_receiver`。

`remotevideo` 不能直接因为 `iio_info` 通过就发射 RF。`iio_info` 只是证明：

```text
E310 的 AD9361 可以被软件控制
```

之后还要实现：

```text
UDP 包 -> IQ buffer -> AD9361 TX
```

## 最小成功判据

本阶段只要满足以下任意一条，就可以继续做 UDP 接收程序：

1. PC 端：

```powershell
.\iio_info.exe -u ip:192.168.1.10
```

能看到 `ad9361-phy` 和 TX/RX buffer 设备。

2. E310 本机：

```bash
iio_info
```

能看到 `ad9361-phy`，并且 PC 能 ping 通 E310。

