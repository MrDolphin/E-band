# E310 TX 生命周期诊断实施计划

> **For agentic workers:** 按仓库 `AGENTS.md` 的阶段门执行；不得跨 Goal 自动继续。实施阶段使用 `superpowers:executing-plans`，每个 Goal 完成后停止并等待用户确认。

**目标：** 在不依赖天线、角反和 RX 同步的条件下，确定 E310 的 DDS/FMCW 持续发射在哪个生命周期阶段停止，并为后续最小修复提供可复现证据。

**架构：** 保留 `diagnose_e310_tx.py` 作为唯一人工在环入口，但将硬件配置、TX 启用、周期状态采样、RX 创建和 RX 读取拆成显式阶段。诊断首先使用完全不创建 RX 缓冲的 TX-only 路径；只有 TX-only 连续稳定后，才逐级引入 RX，从而把驱动/设备问题和应用初始化顺序问题分开。

**技术栈：** Python 3.12、pyadi-iio 0.0.21、libiio、AD9361、`unittest`、示波器人工观察。

## 全局约束

- 已知现场结果：E310 冷启动后仅运行 `/usr/sbin/iiod -D -n 3 -F /dev/iio_ffs`，DDS 与 FMCW 均只闪一下。
- 不运行 `fmcw-v0_976ba638_0722_1102`；其输出证明它是 3.84 MSPS/3 MHz 的非循环 QPSK video bridge，不是 FMCW 后端。
- TX 保持 `-30 dB`、数字幅度 `0.40`、TX channel 0、port B；本计划不提高功率。
- 不修改 chirp 同步阈值、CFAR、背景标定或距离算法。
- 保留 `artifacts/`、缓存、板卡控制资料和现有未跟踪文件，不提交它们。
- Goal 3 只实现一个明确功能；Goal 4 才增加测试并依据证据修复。

---

## Goal 3：已完成 — 实现 TX 生命周期诊断功能

### Task 1：为诊断脚本增加显式阶段和结构化日志

**文件：**

- 修改：`python_sdr_loopback/scripts/diagnose_e310_tx.py`
- 不修改：`python_sdr_loopback/sdr_loopback/radar/sources.py`

**接口：**

- 新增命令参数：`--phase {dds-tx-only,fmcw-tx-only,fmcw-rx-configured,fmcw-rx-read}`。
- 新增命令参数：`--sample-state-s FLOAT`，默认 `1.0` 秒。
- 每次状态采样输出一行 JSON，固定字段为：

```python
{
    "event": "tx_state",
    "elapsed_s": 3.0,
    "phase": "dds-tx-only",
    "tx_enabled_channels": [0],
    "tx_cyclic_buffer": False,
    "tx_buffer_present": False,
    "dds_enabled": [True, False, False, False],
    "hardware_gain_db": -30.0,
}
```

- `dds-tx-only`：直接使用 `adi.ad9361`，启用 DDS 后仅等待和采样状态，绝不调用 `rx()`。
- `fmcw-tx-only`：直接配置 `adi.ad9361`、生成 CPI、创建 cyclic TX buffer，绝不调用 `E310CpiSource.open()` 或 `rx()`。
- `fmcw-rx-configured`：在稳定 TX-only 后仅配置 RX 属性和缓冲参数，不读取 RX。
- `fmcw-rx-read`：最后才执行一次 RX 读取并继续采样 TX 状态。

- [x] **步骤 1：提取统一状态快照**

在脚本中增加只读辅助函数，所有缺失属性以 `null` 表示，不因日志采样终止发射：

```python
def snapshot_tx_state(sdr, *, phase: str, started_at: float) -> dict[str, object]:
    txbuf = getattr(sdr, "_txbuf", None)
    return {
        "event": "tx_state",
        "elapsed_s": round(time.monotonic() - started_at, 3),
        "phase": phase,
        "tx_enabled_channels": list(sdr.tx_enabled_channels),
        "tx_cyclic_buffer": bool(sdr.tx_cyclic_buffer),
        "tx_buffer_present": txbuf is not None,
        "dds_enabled": list(getattr(sdr, "dds_enabled", ())),
    }
```

- [x] **步骤 2：实现真正的 FMCW TX-only 打开路径**

不得复用会执行两次 `rx()` 的 `E310CpiSource.open()`；脚本内部直接完成 TX 配置和上传，波形仍复用生产代码：

```python
config = RadarConfig()
source = E310CpiSource(config, radio_config)
sdr = adi.ad9361(uri=URI)
sdr.sample_rate = int(config.sample_rate_hz)
sdr.tx_rf_bandwidth = int(config.bandwidth_hz)
sdr.tx_lo = radio_config.lo_hz
sdr.tx_enabled_channels = [radio_config.tx_channel]
sdr._set_iio_attr(
    f"voltage{radio_config.tx_channel}",
    "rf_port_select",
    True,
    radio_config.tx_port,
)
sdr._set_iio_attr_float(
    f"voltage{radio_config.tx_channel}",
    "hardwaregain",
    True,
    radio_config.tx_gain_db,
)
time.sleep(pre_upload_s)
sdr.tx_cyclic_buffer = True
sdr.tx(source._tx_iq)
```

在 `fmcw-tx-only` 中到此为止，不得调用 `_discard_startup_rx_buffers()`。

- [x] **步骤 3：实现按阶段逐级引入 RX**

仅在对应 phase 中执行额外动作：

```python
if phase in {"fmcw-rx-configured", "fmcw-rx-read"}:
    sdr.rx_rf_bandwidth = int(config.bandwidth_hz)
    sdr.rx_lo = radio_config.lo_hz
    sdr.rx_enabled_channels = [radio_config.rx_channel]
    sdr.rx_buffer_size = (
        config.cpi_samples + radio_config.sync_margin_samples(config)
    )
if phase == "fmcw-rx-read":
    sdr.rx()
```

每个边界动作前后立即记录 `phase_boundary` 和 `tx_state`，以便示波器闪断时间与日志对应。

- [x] **步骤 4：按固定周期输出状态**

将单次长 `sleep(on_s)` 改为基于单调时钟的短等待循环；循环只观察状态，不重新配置设备：

```python
deadline = time.monotonic() + on_s
while time.monotonic() < deadline:
    print(json.dumps(snapshot_tx_state(sdr, phase=phase, started_at=started_at)))
    time.sleep(min(sample_state_s, max(0.0, deadline - time.monotonic())))
```

- [x] **步骤 5：更新脚本内人工测试说明**

在 `--help` 中明确写出：关闭 GUI、停止自定义 E310 bridge、只保留 `iiod`，并按以下顺序运行：

```powershell
python scripts\diagnose_e310_tx.py --phase dds-tx-only --on-s 10
python scripts\diagnose_e310_tx.py --phase fmcw-tx-only --reuse-buffer --on-s 10
python scripts\diagnose_e310_tx.py --phase fmcw-rx-configured --reuse-buffer --on-s 10
python scripts\diagnose_e310_tx.py --phase fmcw-rx-read --reuse-buffer --on-s 10
```

- [x] **步骤 6：停止并报告改动**

Goal 3 不编写测试、不修改生产采集路径、不提交未经 Goal 4 验证的修复。报告新增参数、文件和待执行的四条命令，然后等待用户确认 Goal 4。

---

## Goal 4：测试、定位并执行最小修复

### Task 2：为生命周期诊断增加离线回归测试

**文件：**

- 修改：`python_sdr_loopback/tests/test_radar_sources.py`
- 可能修复：`python_sdr_loopback/scripts/diagnose_e310_tx.py`

- [ ] **步骤 1：增加参数及 dry-run 测试**

使用参数化循环覆盖四个 phase，断言 dry-run 不实例化 AD9361，并输出所选阶段。

- [ ] **步骤 2：增加 Fake AD9361 调用边界测试**

断言 `dds-tx-only`、`fmcw-tx-only` 与 `fmcw-rx-configured` 的 RX 调用数均为 0，`fmcw-rx-read` 恰为 1；关键断言：

```python
self.assertEqual(device.rx_call_count, 0)
self.assertTrue(device.tx_cyclic_buffer)
self.assertIsNotNone(device._txbuf)
```

- [ ] **步骤 3：运行聚焦测试**

```powershell
python -m unittest tests.test_radar_sources -v
```

预期：生命周期诊断相关测试全部通过。

### Task 3：执行四阶段硬件矩阵并选择唯一修复分支

- [ ] **步骤 1：保证排他状态**

E310 仅保留 `iiod`；GUI 和 video bridge 均停止。每个 phase 运行 10 秒，在 `artifacts/hardware/tx_lifecycle/` 保存 stdout，并记录示波器是“持续”还是“闪一下”。

- [ ] **步骤 2：按首次失败边界选择修复**

1. `dds-tx-only` 已失败：不修改雷达生产代码。进一步使用原生 libiio DDS 小程序区分 pyadi 与驱动/FPGA；若原生路径也失败，记录为设备镜像/FPGA DMA 问题。
2. DDS 持续但 `fmcw-tx-only` 失败：缩短 cyclic buffer（单 chirp、2 chirp、完整 CPI）定位 DMA 长度边界；只修复缓冲长度或上传方式。
3. TX-only 持续但 `fmcw-rx-configured` 失败：修复 TX/RX 属性配置顺序。
4. `fmcw-rx-configured` 持续但 `fmcw-rx-read` 失败：修复 RX 缓冲创建/首次读取后的 TX 恢复或重新上传顺序。

- [ ] **步骤 3：为实际命中的分支先增加失败测试，再实施最小修复**

只修改拥有该行为的文件；若命中 RX 初始化顺序，目标文件为 `python_sdr_loopback/sdr_loopback/radar/sources.py`，不得顺带调整同步阈值或 UI。

- [ ] **步骤 4：验证持续发射验收标准**

- DDS 连续 60 秒，连续启动 10/10 成功。
- FMCW TX-only 连续 60 秒，连续启动 10/10 成功。
- FMCW TX+RX 连续 60 秒，示波器无闪断。
- 随后保存 20 个 RX CPI；不削顶，并能观察到明显高于随机噪声基线的 chirp correlation。

- [ ] **步骤 5：运行完整测试并提交**

```powershell
python -m unittest discover -s tests -v
```

完整测试通过后，仅提交相关脚本、测试和必要的生产修复；不提交 `artifacts/`。推送当前分支，并在现有 PR 中记录命中边界、示波器证据、修复和测试结果。
