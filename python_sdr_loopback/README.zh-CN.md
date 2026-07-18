# Python SDR RF 回环

这是一个用于单台 ANTSDR E310 RF 回环测试的干净 Python SDR modem 工程。

目标链路：

```text
payload bytes
  -> packet header + CRC32
  -> QPSK symbols
  -> root-raised-cosine pulse shaping
  -> AD9361 TX
  -> coax + attenuator
  -> AD9361 RX
  -> matched filter + preamble sync
  -> QPSK decisions
  -> packet + CRC check
```

建议先从纯软件回环开始：

```powershell
python python_sdr_loopback/scripts/simulate_loopback.py --message "hello e310"
```

硬件 RF 回环连接方式：

```text
E310 TX1 SMA -> 30 to 60 dB attenuation -> E310 RX1 SMA
```

不要在没有衰减器的情况下直接把 TX 接到 RX。

硬件脚本需要 `pyadi-iio`，并且 PC 能通过 IIO 访问 E310，通常使用：

```powershell
python -m pip install pyadi-iio
python python_sdr_loopback/scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --message "hello rf"
```

第一个硬件目标是保守的低速 QPSK 链路，用来在加入视频或 IP 等更高层功能前，先测量帧恢复能力和 CRC 通过率。

已验证的 RF 回环基线命令：

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --message "hello rf" --tx-gain-db -25 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --packet-count 100 --save-iq artifacts\baseline_100_ok.npz --plot-prefix artifacts\baseline_100_ok
```

分析保存的采集文件：

```powershell
python scripts/analyze_capture.py artifacts\baseline_100_ok.npz
```

常用字段：

- `packets_ok` / `packet_error_rate`：分组恢复结果。
- `theoretical_rrc_bandwidth_hz`：QPSK/RRC 理论占用带宽。
- `occupied_bw_99_hz` 和 `threshold_bw_minus_20db_hz`：实测频谱宽度。
- `evm_rms_percent` 和 `snr_est_db`：粗略星座质量指标。

15 Mbps 原始 QPSK 载荷测试：

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --sample-rate 30000000 --symbol-rate 7500000 --bandwidth 20000000 --tx-gain-db -12 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --packet-count 100 --payload-pattern counter --payload-bytes 512 --save-iq artifacts\payload_512_15mbps.npz --plot-prefix artifacts\payload_512_15mbps
python scripts/analyze_capture.py artifacts\payload_512_15mbps.npz
```

在 payload 测试中，`payload_bitrate_est_bps` 是扣除前导码、分组头和 CRC 开销后的估计可用速率。`payload_bitrate_ok_bps` 还会进一步考虑实际成功恢复的分组。

在高采样率下，短帧配合 `--tx-cyclic-copies 3` 最可靠，这和最初的 15 Mbps 基线一致。长 payload 帧更敏感；如果 TX 静默失败，通常会表现为 `rx_rms_dbfs` 接近 -62 dBFS。默认 `--rx-discard-buffers 0` 也和该基线一致。

较长 payload 测试建议保持每个 TX 帧较小，并分多个批次运行：

```powershell
python scripts/run_payload_batches.py --batches 20 --packets-per-batch 5 --payload-bytes 512 --tx-cyclic-copies 2 --artifact-prefix artifacts\payload_512_100_15mbps_batched
```

同一 RF 回环链路上的文件传输冒烟测试：

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --sample-rate 30000000 --symbol-rate 7500000 --bandwidth 20000000 --tx-gain-db -12 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --input-file artifacts\test_input.bin --output-file artifacts\test_output.bin --payload-bytes 512 --tx-cyclic-copies 3 --rx-discard-buffers 0 --min-rx-rms-dbfs -45 --rx-level-retries 8 --save-iq artifacts\file_transfer_15mbps.npz --plot-prefix artifacts\file_transfer_15mbps
```

每个 512 字节 SDR payload 会携带一个小的文件分片头和文件数据。所有分片恢复完成且整文件 CRC32 匹配后，会输出 `file_ok=true`。

对于较大的图片或小视频文件，避免一次性使用巨大的 cyclic TX buffer。应使用有界批次，并让脚本拼接已验证的分片：

```powershell
python scripts/run_file_transfer_batches.py --input-file phone.mp4 --output-file artifacts\recovered_phone.mp4 --batch-bytes 200000 --artifact-prefix artifacts\phone_batched
```

## FMCW 雷达 MVP 验证

FMCW 雷达链路支持仿真、IQ 回放和 E310 采集。上位机的配置档位将稳定的 20 MHz、验证用 40 MHz 与 56 MHz 极限实验分开；选择档位只更新界面参数，下一次启动雷达时才会重新配置 E310。

配置扫频带宽为 `B` 时，软件距离单元间隔为 `c / (2B)`：20 MHz 时约为 7.49 m，56 MHz 时约为 2.68 m。这里是配置的基带带宽；在将其解释为实际物理距离分辨率前，必须用仪器确认外部倍频链的有效 RF 扫频带宽。

生成并分析可复现的测试数据：

```powershell
python scripts/generate_fmcw_fixture.py --output artifacts\fmcw_acceptance.npz --target 22.5,1.2,18 --seed 7
python scripts/analyze_fmcw_capture.py artifacts\fmcw_acceptance.npz --output-dir artifacts\fmcw_acceptance_result
```

执行 5 分钟无界面合成源稳定性测试。该命令不会连接 E310；每个 CPI 写入一条诊断 JSON，汇总写入 `soak_summary.json`：

```powershell
python scripts/run_fmcw_radar.py --source synthetic --duration-sec 300 --headless --metrics artifacts\fmcw_soak\metrics.jsonl
```

通过条件为：`frames_failed=0`、`queue_max_depth=1`、`controller_stopped=true`、`soak_ok=true`。E310 已重新接线后，可先用下列命令确认配置而不访问硬件：

```powershell
python scripts/diagnose_fmcw_sync.py --dry-run --frames 10 --bandwidth-hz 56000000
```
