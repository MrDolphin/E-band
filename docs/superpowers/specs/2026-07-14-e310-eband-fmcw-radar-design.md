# E310 + E波段 FMCW 雷达上位机设计

日期：2026-07-14  
状态：已完成方案确认，等待用户审阅  
目标分支：`python-feature/fmcw-v0`

## 1. 目标

在现有 `python_sdr_loopback` Python 上位机中增加 FMCW 雷达能力：由 ANTSDR E310 产生复数基带 FMCW 波形，经 E波段收发器搬移到 76 GHz，通过一个发射喇叭和一个接收喇叭感知室内目标。上位机显示目标距离、径向速度、SNR、目标列表、半圆雷达图以及 2D FFT 距离-多普勒图。

首版使用单接收通道，不能真实测量方位角。目标模型保留 `azimuth_deg` 字段，但其值必须为空，界面明确显示“单RX：方位角未测量”。以后接入相控阵或多路相干接收后，再实现波束形成和方位FFT。

## 2. 首版范围

首版包含：

- 时域复数IQ合成目标仿真；
- 保存IQ文件离线回放；
- FMCW波形生成、chirp同步、数字去斜；
- 距离FFT、多普勒FFT、2D CFAR、目标聚类和SNR估计；
- 系统配置页中的FMCW参数区；
- 独立的“FMCW雷达”页签；
- 半圆雷达图、距离-多普勒图、目标表和诊断状态；
- E310有限CPI采集接口；
- 空场背景校准和异常IQ保存。

首版不包含：

- 真实方位角测量；
- 目标跟踪滤波和跨帧ID保持；
- FPGA实时FFT；
- E310端C/C++雷达处理；
- 无安全衰减条件下的TX/RX电缆直连；
- 使用E波段芯片内部5 GHz PLL扫频。

## 3. 场景和初始参数

首版面向室内近距离验证：

| 参数 | 初始值 |
|---|---:|
| 目标距离范围 | 5–50 m |
| 目标径向速度范围 | ±5 m/s |
| E波段中心频率 | 76 GHz，可配置 |
| E310复数IQ采样率 | 30 MSPS |
| FMCW扫频带宽 | 20 MHz |
| 有效chirp时宽 | 128 μs |
| chirp空闲时间 | 16 μs |
| chirp周期 | 144 μs |
| 每个CPI chirp数 | 64 |
| 每个有效chirp采样点 | 3840 |
| 每个chirp总采样点 | 4320 |
| 距离FFT长度 | 4096 |
| CPI时长 | 9.216 ms |

派生指标：

- 波长：`λ = c / fc ≈ 3.95 mm`；
- 距离分辨率：`ΔR = c / (2B) = 7.5 m`；
- 最大无模糊径向速度：`vmax = λ / (4Tchirp) ≈ 6.85 m/s`；
- 速度分辨率：`Δv = λ / (2NTchirp) ≈ 0.21 m/s`。

这些指标必须由配置对象实时计算，GUI不得维护第二套硬编码结果。由于复数IQ包含I和Q两路，30 MSPS表示每秒3000万个复数样点；按I/Q各16位传输时，原始数据量约120 MB/s。首版只采集有限CPI，不承诺持续传回30 MSPS原始IQ。每个CPI包含276480个复数样点，int16 IQ约1.11 MB；3840个有效样点补零到4096点FFT，比4800点补零到8192点更适合Python实时处理，同时保持20 MHz扫频带宽和7.5 m真实距离分辨率不变。

## 4. 总体架构

数据流：

```text
RadarConfig
  -> FmcwWaveformGenerator
  -> RadarDataSource
       -> SyntheticTargetSource
       -> IqReplaySource
       -> E310CpiSource
  -> ChirpSynchronizer
  -> FmcwProcessor
       -> digital dechirp
       -> range FFT
       -> static clutter suppression
       -> Doppler FFT
  -> CfarDetector
  -> TargetClusterer
  -> RadarFrame
  -> RadarController
  -> Tkinter GUI
```

GUI、数据源和信号处理之间只能通过明确的数据模型通信。真实E310数据、IQ回放和合成目标必须进入同一个 `FmcwProcessor`，禁止为仿真模式绕过算法直接生成目标列表或热力图。

## 5. 代码边界

新增包：

```text
python_sdr_loopback/sdr_loopback/radar/
  config.py
  models.py
  waveform.py
  sources.py
  simulator.py
  synchronizer.py
  processor.py
  detector.py
  calibration.py
  storage.py
  controller.py
```

职责：

- `config.py`：参数类型、范围校验和派生指标；
- `models.py`：`RadarTarget`、`RadarFrame`、`RadarDiagnostics`、真值模型；
- `waveform.py`：复数基带chirp和TX参考CPI；
- `sources.py`：仿真、回放、E310采集数据源协议；
- `simulator.py`：基于距离、速度、SNR生成时域RX IQ；
- `synchronizer.py`：利用空闲段和相关峰定位chirp边界；
- `processor.py`：去斜、窗函数、距离FFT、多普勒FFT；
- `detector.py`：2D CA-CFAR、局部峰值、聚类和SNR估计；
- `calibration.py`：固定系统延迟、静态背景和泄漏保护区；
- `storage.py`：NPZ、JSON和矩阵文件读写；
- `controller.py`：后台工作线程、最新帧队列和GUI状态。

新增脚本：

```text
python_sdr_loopback/scripts/run_fmcw_radar.py
python_sdr_loopback/scripts/analyze_fmcw_capture.py
python_sdr_loopback/scripts/generate_fmcw_fixture.py
```

现有 `scripts/sdr_video_gui.py` 只负责页签、控件绑定和渲染，不放入FFT或CFAR实现。

## 6. 数据模型

`RadarTarget` 至少包含：

```text
target_id
range_m
radial_velocity_mps
azimuth_deg
snr_db
power_db
range_bin
doppler_bin
confidence
timestamp
```

首版 `azimuth_deg = None`。雷达图将目标绘制在0°参考线上，但目标旁和页签顶部必须显示“方位角未测量”，避免将显示位置误认为测角结果。

`RadarFrame` 至少包含：

```text
frame_index
timestamp
config_snapshot
range_axis_m
velocity_axis_mps
range_doppler_db
targets
diagnostics
```

目标表、半圆雷达图和距离-多普勒图必须使用同一个 `frame_index`，禁止显示来自不同CPI的数据。

## 7. 合成目标仿真

仿真器生成完整时域复数IQ：

```text
rx(t) = Σ Ak · tx(t - 2Rk/c) · exp(j2πfdk t) + leakage + clutter + noise
fdk = 2vk / λ
```

要求：

- 支持多个目标；
- 支持分数采样延迟，不能只按整数样点移动；
- 根据目标SNR生成复高斯噪声；
- 支持零距离泄漏和静态杂波；
- 支持固定随机种子，保证自动化测试可重复；
- 仿真结果保存真实目标参数，但处理器不得读取真值；
- GUI只显示处理器检测结果，不直接显示输入目标。

首批场景：单个静止目标、单个运动目标、两个目标、同距离不同速度、弱目标、纯噪声、强零距离泄漏加远处目标。

## 8. 信号处理

每个CPI的处理顺序固定为：

1. 检查样点数量和有限值；
2. 定位chirp边界并验证64个chirp完整性；
3. 使用 `rx × conj(tx_reference)` 数字去斜；
4. 去直流、应用空场背景和近距离保护区；
5. 快时间方向应用Hann窗并执行距离FFT；
6. 慢时间方向去均值、应用Hann窗并执行多普勒FFT及FFT shift；
7. 转换为相对功率dB矩阵；
8. 运行2D CA-CFAR；
9. 进行局部峰值筛选和相邻单元聚类；
10. 根据坐标轴换算距离、速度并估算局部SNR。

FFT长度采用不小于有效数据长度的2次幂。补零只用于改善显示和峰值插值，不得把补零后的bin间距描述为真实物理分辨率。

## 9. GUI设计

现有Notebook新增第三个页签“FMCW雷达”。

### 9.1 系统配置页

在当前配置页下半部增加“FMCW雷达配置”：

- 数据源：合成目标 / IQ回放 / E310实时；
- 工作模式；
- 中心频率、复数IQ采样率、扫频带宽；
- 有效chirp时宽、空闲时间、每CPI chirp数；
- TX/RX通道、TX/RX增益；
- CFAR门限和最大显示距离；
- 自动计算距离分辨率、最大速度、速度分辨率和CPI时长；
- 应用参数、保存预设、加载IQ回放；
- 合成目标编辑表。

### 9.2 FMCW雷达页

顶部提供开始、停止、保存当前CPI和运行状态。

主要区域：

- 左侧标准半圆雷达图；
- 距离环标注10/20/30/40/50 m；
- 方位角刻度标注-90°至+90°；
- 横轴标注方位角，纵向/径向标注距离；
- 单RX目标全部绘制在0°参考线上；
- 右侧2D FFT距离-多普勒图；
- 横轴为距离m，纵轴为径向速度m/s；
- 显示网格至少64×32，实际矩阵更高时按窗口尺寸抽样；
- 下方目标列表和采集/处理诊断区。

Tkinter主线程只渲染。后台线程生成或采集IQ并处理，通过容量为1的队列提交最新 `RadarFrame`。处理跟不上时丢弃旧显示帧，不允许积累延迟。

## 10. 文件格式

NPZ采集文件包含：

```text
tx_iq
rx_iq
radar_config_json
truth_targets_json（仅仿真文件）
capture_timestamp
```

处理输出包含：

```text
result.json
range_doppler.npy
metrics.jsonl
```

`result.json`保存检测目标和参数快照；`metrics.jsonl`逐CPI记录同步、电平、削顶、噪声底、检测数量和处理耗时。

## 11. 状态和错误处理

- 无目标：正常显示“当前无目标”；
- 参数非法：禁止启动并指出字段；
- chirp同步失败：不输出目标并保存诊断IQ；
- RX过低：黄色提示；
- RX削顶：红色提示并建议降低增益；
- CPI缺少chirp：丢弃该CPI；
- 处理超时：丢弃旧显示帧，记录overrun；
- IIO异常：停止收发、释放缓冲区并允许重连；
- 相位稳定性不足：距离可显示，速度标记为不可信；
- 硬件停止或窗口关闭：始终销毁循环TX和RX缓冲区。

## 12. 硬件安全和校准

在没有足够衰减器时，禁止以6 dB衰减器执行TX/RX电缆直连。6 dB只能改变幅度，不能模拟目标距离或速度，而且可能使RX过载。

真实模式按以下顺序启用：

1. 合成IQ算法通过；
2. 保存IQ回放通过；
3. 使用两个空间喇叭、合理隔离和低TX增益；
4. 采集空场背景并估计静态泄漏；
5. 放置金属反射板验证静止目标；
6. 移动反射板或人员验证速度；
7. 连续运行和多目标测试。

真实速度依赖76 GHz载频和chirp间相位稳定性。系统必须记录chirp间相关度或相位一致性诊断；未满足阈值时不得将速度标为可信。

## 13. 测试与验收

自动化测试：

- 参数公式与边界校验；
- chirp瞬时频率、带宽和时宽；
- 分数延迟和多普勒相位；
- 单目标和多目标FFT坐标；
- CFAR弱目标、纯噪声和泄漏场景；
- NPZ/JSON往返一致性；
- GUI帧队列不会积压。

算法验收：

- 距离误差不超过1个真实距离bin；
- 速度误差不超过1个真实多普勒bin；
- SNR误差不超过3 dB；
- 纯噪声场景不持续产生目标；
- 目标表、雷达图和热力图帧号一致；
- 仿真模式连续运行5分钟无GUI卡死；
- 默认配置单CPI处理时间低于50 ms。

硬件验收：

- RX无削顶，`rx_clip_ratio`接近0；
- 64个chirp能够稳定同步；
- 空场背景可重复；
- 增加静态反射板后距离谱出现可重复新峰；
- 运动目标的多普勒方向和实际运动方向一致；
- 异常退出后IIO缓冲区可再次创建。

## 14. 实施阶段

1. 参数、模型、波形和派生指标；
2. 时域合成目标和测试夹具；
3. 去斜、距离FFT、多普勒FFT；
4. CFAR、聚类和SNR；
5. CLI分析与IQ回放；
6. 配置页FMCW参数区；
7. FMCW雷达页V2布局；
8. E310有限CPI数据源；
9. 空场校准和空间目标验证；
10. 性能优化及E310 C/C++迁移评估；
11. 相控阵接入后增加多通道校准、波束形成和方位FFT。

每个阶段必须先通过对应的自动化或硬件检查，再进入下一阶段。首版成功的核心判据是：由时域IQ经过完整处理链重新检测出合成目标，而不是把输入目标参数直接传给GUI。
