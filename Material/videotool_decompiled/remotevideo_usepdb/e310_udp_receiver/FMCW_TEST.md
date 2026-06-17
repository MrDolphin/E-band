# E310 FMCW 测试说明

## 当前参数

```text
TX/RX 本振频率      200 MHz
射频扫频范围        190～210 MHz
基带扫频范围        -10～+10 MHz
采样率              30.72 MSPS
AD9361 RF 带宽      28 MHz
TX hardwaregain     -10 dB
Chirp 扫频带宽      20 MHz
每个 PRT 采样点数   6144
有效 Chirp 点数     5530
空闲点数            614
有效 Chirp 时间     约 180.013 us
空闲时间            约 19.987 us
淡入/淡出时间       约 2 us
PRT                 200 us
PRF                 5 kHz
Chirp 斜率          约 111.103 GHz/s
理论距离分辨率      约 7.49 m
```

一个周期由 `180 us` 上扫 Chirp 和 `20 us` 零 IQ 空闲段组成。波形使用
cyclic buffer 连续重复。

## 部署 E310 程序

先停止当前进程：

```powershell
ssh root@192.168.1.10 "killall udp_receiver_fmcw"
```

将新版程序复制到持久化 SD 卡：

```powershell
scp -O "D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\e310_udp_receiver\udp_receiver_fmcw" root@192.168.1.10:/mnt/sdcard/
ssh root@192.168.1.10 "sync; reboot"
```

重启后检查：

```powershell
ssh root@192.168.1.10 "cat /tmp/fmcw.log"
```

正常启动后应看到：

```text
E310 FMCW bridge listening on 0.0.0.0:8080
TX: cyclic cf-ad9361-dds-core-lpc, RX: cf-ad9361-lpc -> UDP type 0x03
```

上位机发送后应继续看到：

```text
complete TX frame=... samples=6144
cyclic TX started: samples=6144 duration=0.200 ms
```

## 频谱仪设置

```text
中心频率       200 MHz
扫宽           30 MHz
RBW            100 kHz
VBW            100 kHz
参考电平       0 dBm
输入衰减       20～30 dB
轨迹           Max Hold
```

正常情况下主要能量应覆盖约 `190～210 MHz`。

## 示波器设置

```text
输入阻抗       50 ohm
带宽限制       关闭
采样率         尽可能高
时基           20 us/div，先观察完整 PRT
触发           CH1 边沿或包络触发
```

需要观察射频周期时，将时基缩小到约 `2～5 ns/div`。在一次 Chirp 内，射频
周期会从约 `5.26 ns` 逐渐缩短到约 `4.76 ns`。
