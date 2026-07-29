# E310 FMCW 硬件恢复与分档联调手册

本手册只在用户已确认 E310、两块 E 波段板、两个喇叭和角反均已正确连接并允许发射时执行。硬件未连接时，只可运行文中的 `--dry-run` 命令。

## 目标与顺序

按 **20 MHz 稳定基线 → 40 MHz 验证 → 56 MHz 极限验证** 的顺序推进。每一档都先确认同步和无削顶，再做空场/角反 A-B-A；前一档没有稳定证据时，不进入下一档。

| 档位 | CLI 名称 | 采样率 | 有效采样/chirp | 距离 FFT | 软件距离单元 |
| --- | --- | ---: | ---: | ---: | ---: |
| 稳定基线 | `stable-20` | 30 MSPS | 3840 | 4096 | 约 7.49 m |
| 验证 | `validate-40` | 61.44 MSPS | 7680 | 8192 | 约 3.75 m |
| 极限 | `limit-56` | 61.44 MSPS | 7680 | 8192 | 约 2.68 m |

这里的距离单元是配置带宽的**软件量化结果**。外部八倍频链的实际 RF 扫频带宽必须用频谱仪确认，才可将其解释为物理距离分辨率。

## 0. 上电前与安全检查

1. 确认 TX 板与 RX 板已分别完成正确的串口初始化，PA、LNA、PLL/倍频链处于预期状态。
2. 确认 E310 使用 TX1/RX1 对应的已验证端口配置；本项目的默认射频端口为 `B` / `B_BALANCED`，若现场硬件不同，必须显式传入端口参数。
3. 喇叭以面对前方的单 TX、单 RX 方式布置，两个喇叭及角反固定后再采集。单 RX 不提供可信方位角。
4. 不要用普通 SMA 同轴或仅 6 dB、12 dB 衰减直连 TX/RX 做发射测试；若必须进行有线排障，应采用额定覆盖频段且衰减量足够的毫米波衰减/耦合方案。
5. 先用低且已验证的发射设置。不要在同步不稳定时连续扫增益；每次只改变一个变量，并保存诊断目录。

## 1. 离线预检（不访问设备）

在 `python_sdr_loopback` 目录执行：

```powershell
python scripts\diagnose_fmcw_sync.py --dry-run --frames 10 --profile stable-20
python scripts\diagnose_fmcw_sync.py --dry-run --frames 10 --profile validate-40
python scripts\diagnose_fmcw_sync.py --dry-run --frames 10 --profile limit-56
python scripts\run_fmcw_radar.py --source e310 --profile limit-56 --dry-run
```

三条同步预检应分别显示正确的 `sample_rate_hz`、`bandwidth_hz` 与 `cpi_samples`，并显示 `hardware_access=false`。56 MHz 的关键值为 61.44 MSPS、56 MHz、552960 CPI 采样。

## 2. 20 MHz 稳定基线

硬件已连接且允许发射后，先收集 20 个 CPI 同步诊断。下面以此前经现场验证过的低风险起点为例；若当前场地已确认其他安全工作点，使用现场工作点并记录原因。

```powershell
python scripts\diagnose_fmcw_sync.py --frames 20 --profile stable-20 --tx-gain-db -20 --tx-amplitude 0.40 --rx-gain-db 20 --save-captures failures --output-dir artifacts\hardware\stable20_sync
```

检查 `sync_metrics.jsonl`：

- 每帧应记录 `sync_ok`、相关性、周期相干、RMS、峰值和削顶比例；
- 出现失败时，对应原始 IQ 会写入 `captures/`，可用回放/分析脚本离线复查；
- 出现明显削顶、持续同步失败或板卡状态异常时，停止本档，不要直接进入 40/56 MHz。

## 3. 空场/角反 A-B-A

在天线位置固定、人员离开主波束后，按同一档位连续完成：

1. **A1 空场**：采集背景与诊断；
2. **B 角反**：放入 20 cm 角反，保持正对波束，采集相同数量 CPI；
3. **A2 空场**：移走角反，再次采集。

在 GUI 中使用“采集空场背景”，仅在同步正常时开始；随后使用位置候选记录功能保存每次候选、诊断和用户提供的喇叭俯视照片。20 MHz 时，4 m 与 10 m 目标可能分别量化到 7.03 m 或 14.05 m 等距离单元；判断依据是 B 相比 A1/A2 是否出现可重复、可逆的新峰，不是将单元中心误当精确距离。

在同一档位可使用：

```powershell
python scripts\run_fmcw_radar.py --source e310 --profile stable-20 --frames 3 --output-dir artifacts\hardware\stable20_frame --capture-dir artifacts\hardware\stable20_raw
```

运行期不要同时打开多个控制 E310 的脚本或 GUI。需要分析已保存 capture 时，先停止采集，再使用回放/分析命令。

## 4. 40 MHz 与 56 MHz 分档验证

只有 stable-20 的同步、空场与角反 A-B-A 都可重复时，依次替换档位：

```powershell
python scripts\diagnose_fmcw_sync.py --frames 20 --profile validate-40 --save-captures failures --output-dir artifacts\hardware\validate40_sync
python scripts\diagnose_fmcw_sync.py --frames 20 --profile limit-56 --save-captures failures --output-dir artifacts\hardware\limit56_sync
```

每次切换后，先用频谱仪确认发射链存在预期扫频能量且不存在异常强杂散，再进行角反 A-B-A。当前开发电脑上 40/56 MHz 单 CPI 合成处理约 43 ms，而 CPI 时长约 9 ms；界面采用最新帧优先，不能将显示帧率视为逐 CPI 无丢帧处理的承诺。

## 5. 完成记录

每个通过档位至少保留：

- 对应 `sync_metrics.jsonl`；
- A1/B/A2 的 capture 目录或可回放 NPZ；
- 角反距离、喇叭相对位置/朝向、现场照片和 TX/RX 设置；
- 无削顶、同步成功比例、目标单元相对空场变化的简短结论。

这些硬件 artifacts 默认不提交 Git。确认某一版本在 E310 硬件上稳定工作后，再由用户明确要求创建硬件已知良好标签。
