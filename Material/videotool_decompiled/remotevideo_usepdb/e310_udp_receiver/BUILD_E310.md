# E310 构建说明

## 为什么 E310 上 gcc 找不到

`-sh: gcc: not found` 表示当前 E310 固件没有安装本机编译器。ANTSDR E310 是 Zynq-7020，CPU 是 32 位 ARM Cortex-A9，常规做法是在电脑上交叉编译，然后把二进制文件复制到 E310。

## 第一步：在 E310 上确认架构和 libiio

```bash
uname -m
find / -name iio.h 2>/dev/null
find / -name "libiio.so*" 2>/dev/null
```

期望：

```text
uname -m -> armv7l
能找到 libiio.so
```

如果找不到 `iio.h`，说明 E310 只有运行库，没有开发头文件。需要从 ANTSDR/ADI 的 SDK、sysroot 或源码包里取得 libiio 头文件。

## 第二步：在 Ubuntu/WSL 安装交叉编译器

```bash
sudo apt update
sudo apt install -y gcc-arm-linux-gnueabihf make pkg-config
```

## 第三步：准备 target sysroot

在 Windows 创建目录：

```powershell
New-Item -ItemType Directory -Force D:\hp-laptop\E-band\target-sysroot\usr\include
New-Item -ItemType Directory -Force D:\hp-laptop\E-band\target-sysroot\usr\lib
```

从 E310 拷贝 libiio 相关文件。因为你的 E310 没有 sftp-server，scp 要加 `-O`：

```powershell
scp -O root@192.168.1.10:/usr/include/iio.h D:\hp-laptop\E-band\target-sysroot\usr\include\
scp -O root@192.168.1.10:/usr/lib/libiio.so* D:\hp-laptop\E-band\target-sysroot\usr\lib\
```

如果实际路径不是 `/usr/include` 或 `/usr/lib`，按 `find` 命令查到的路径替换。

## 第四步：交叉编译

在 WSL/Ubuntu 中进入源码目录：

```bash
cd /mnt/d/hp-laptop/E-band/Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
```

编译：

```bash
arm-linux-gnueabihf-gcc udp_receiver.c \
  -o udp_receiver \
  -I/mnt/d/hp-laptop/E-band/target-sysroot/usr/include \
  -L/mnt/d/hp-laptop/E-band/target-sysroot/usr/lib \
  -liio -lpthread -lm
```

检查输出文件：

```bash
file udp_receiver
```

期望看到类似：

```text
ELF 32-bit LSB executable, ARM, EABI5
```

## 第五步：复制并运行

Windows PowerShell：

```powershell
scp -O D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\udp_receiver root@192.168.1.10:/root/
```

E310：

```bash
chmod +x /root/udp_receiver
/root/udp_receiver
```

## 如果运行时报 libiio 找不到

先检查：

```bash
find / -name "libiio.so*" 2>/dev/null
echo $LD_LIBRARY_PATH
```

如果库在特殊目录，例如 `/usr/local/lib`，运行前加：

```bash
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
/root/udp_receiver
```

## 如果编译报 iio_buffer_first 类型错误

说明你使用的 libiio 版本 API 和当前代码不一致。当前代码已经按旧版常见用法传入 TX I 通道：

```c
iio_buffer_first(txbuf, tx_i)
```

如果仍然报错，把完整编译错误贴出来，再按你 E310 的 libiio 版本调整。
