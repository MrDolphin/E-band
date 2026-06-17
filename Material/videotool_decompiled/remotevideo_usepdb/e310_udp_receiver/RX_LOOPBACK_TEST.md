# E310 RX 有线回环测试

## 1. 硬件连接

```text
E310 TX1
  -> 30～50 dB 固定射频衰减器
  -> E310 RX1
```

禁止使用普通 SMA 线直接连接 TX1 和 RX1。测试前关闭 FPGA DDS：

```bash
for ch in 0 1 2 3 4 5 6 7; do
    iio_attr -c cf-ad9361-dds-core-lpc -o altvoltage$ch scale 0
done
```

启动 UDP 程序后确认：

```text
E310 FMCW bridge listening on 0.0.0.0:8080
TX: TX1 cyclic ...
```

## 2. 上位机

使用：

```text
bin/rx-input-analyzer/remotevideo.exe
```

通信参数：

```text
目标地址：192.168.1.10
目标端口：8080
监听端口：8098
文件路径：空
```

## 3. 固定单音RX测试

### 模式5

模式5发送约 `195 MHz`。RX本振为 `200 MHz`，状态栏预期：

```text
RF=约195 MHz
baseband=约-5 MHz
```

### 模式6

模式6发送约 `205 MHz`，状态栏预期：

```text
RF=约205 MHz
baseband=约+5 MHz
```

同时显示：

```text
RMS：接收IQ有效值
peak：接收IQ峰值
PAPR：峰均比
```

若频率正确但 `peak` 接近 `1.0`，接收可能过载，应增加外部衰减。

## 4. FMCW RX测试

选择模式3。状态栏显示：

```text
Chirp方向
接收起始频率
接收终止频率
接收扫频带宽
有效扫频时间
空闲时间
PRT
线性拟合误差
拍频和未校准距离
```

理想回环结果接近：

```text
方向：up
起点：190 MHz
终点：210 MHz
带宽：20 MHz
有效时间：180 us
空闲时间：20 us
PRT：200 us
```

短同轴回环的延迟很小，因此拍频和距离应靠近零。当前TX/RX没有硬件帧同步，
拍频距离可能漂移；频率、带宽、有效时间和PRT测量仍可用于验证接收链路。

## 5. 故障判断

```text
没有RX状态更新：
检查E310日志是否持续出现 RX IQ returned。

195/205 MHz符号反了：
检查I/Q顺序或RX通道映射。

频率正确但幅度跳动：
检查接头、衰减器和RX自动增益。

FMCW无法找到空闲段：
增加衰减，避免RX饱和；确认模式3包含20 us零IQ空闲。
```
