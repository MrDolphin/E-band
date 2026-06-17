# E310 UDP Receiver

这个目录用于方案 B：

```text
Windows remotevideo.exe
  -> UDP 192.168.1.10:8080
  -> E310 udp_receiver
  -> libiio
  -> AD9361 TX
```

## 目前结论

你在 E310 上执行：

```bash
gcc udp_receiver.c -o udp_receiver -liio
```

提示：

```text
-sh: gcc: not found
```

这说明当前 E310 固件里没有本机 gcc 编译器。这个不是源码语法错误，而是嵌入式 Linux 镜像通常不会预装完整编译环境。

正确做法是：在电脑或 Ubuntu/WSL 上交叉编译 ARM 版本的 `udp_receiver`，然后拷贝到 E310 运行。

## 先确认 E310 环境

登录 E310：

```powershell
ssh root@192.168.1.10
```

默认密码通常是：

```text
analog
```

在 E310 上执行：

```bash
uname -a
uname -m
find / -name iio.h 2>/dev/null
find / -name "libiio.so*" 2>/dev/null
find / -name "libiio.a" 2>/dev/null
```

如果 `uname -m` 是 `armv7l`，它就是 Zynq-7020 的 32 位 ARM Linux，交叉编译器通常选择：

```text
arm-linux-gnueabihf-gcc
```

## 交叉编译思路

在 Ubuntu/WSL 里安装工具链：

```bash
sudo apt update
sudo apt install -y gcc-arm-linux-gnueabihf make pkg-config
```

然后需要准备 ARM 版本的 libiio 头文件和库文件：

```text
include/iio.h
lib/libiio.so 或 lib/libiio.a
```

如果 E310 上能找到 `/usr/include/iio.h` 和 `/usr/lib/libiio.so`，可以先从 E310 拷贝出来作为交叉编译依赖。

示例：

```powershell
scp -O root@192.168.1.10:/usr/include/iio.h D:\hp-laptop\E-band\target-sysroot\usr\include\
scp -O root@192.168.1.10:/usr/lib/libiio.so* D:\hp-laptop\E-band\target-sysroot\usr\lib\
```

再在 WSL/Ubuntu 中编译：

```bash
arm-linux-gnueabihf-gcc udp_receiver.c \
  -o udp_receiver \
  -I/mnt/d/hp-laptop/E-band/target-sysroot/usr/include \
  -L/mnt/d/hp-laptop/E-band/target-sysroot/usr/lib \
  -liio -lpthread -lm
```

把生成的 ARM 程序传回 E310：

```powershell
scp -O D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\udp_receiver root@192.168.1.10:/root/
```

在 E310 上运行：

```bash
chmod +x /root/udp_receiver
/root/udp_receiver
```

## remotevideo 设置

Windows 上位机设置：

```text
RemoteEndAddr = 192.168.1.10
RemoteEndPort = 8080
LocalRecvPort = 8098 或其他未占用端口
VideoFilePath = 空，或者选择非 .mp4 的 .iq/.bin 文件
CommMode = 0 表示 QPSK
CommMode = 1 表示 16QAM
```

点击开始发送后，E310 端应该看到类似：

```text
received IQ frame=1 chunk=1/1 samples=...
```

## remotevideo IQ 包格式

公共包头：

```text
offset  size  value
0       4     01 DC EF 18
4       1     packetType = 0x00，通信/IQ 数据
5       1     protocolVersion
6       9     reserved
15      2     contentLength，小端
17      N     content
```

IQ content：

```text
offset  size  field
0       4     frameId，int32，小端
4       2     chunkIndex，uint16，小端
6       2     chunkCount，uint16，小端
8       2     sampleFormat，uint16，1 表示 int16 IQ
10      2     sampleCount，uint16
12      N     IQ payload，I0,Q0,I1,Q1...，int16 小端交织
```

## 当前限制

当前 `udp_receiver.c` 是最小验证版：

- 只处理 `packetType = 0x00` 的 IQ 包。
- 只支持 `sampleFormat = 1` 的 int16 IQ。
- 每收到一个 UDP 包，就写一次 AD9361 TX buffer。
- 暂不处理视频文件重组。
- 暂不处理波控包。
- 暂不处理 E310 RX 接收回传。

跑通链路后，再扩展视频文件分片重组、OFDM/QPSK/16QAM 调制、相控阵波控命令和 RX 回传。
