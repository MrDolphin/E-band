# Python SDR RF Loopback

Clean Python SDR modem for single-device ANTSDR E310 RF loopback.

Goal:

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

Start with the software loopback first:

```powershell
python python_sdr_loopback/scripts/simulate_loopback.py --message "hello e310"
```

Hardware RF loopback will use:

```text
E310 TX1 SMA -> 30 to 60 dB attenuation -> E310 RX1 SMA
```

Do not connect TX directly to RX without attenuation.

The hardware script expects `pyadi-iio` and an IIO-reachable E310, usually:

```powershell
python -m pip install pyadi-iio
python python_sdr_loopback/scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --message "hello rf"
```

The first hardware target is a conservative low-rate QPSK link. It is meant to
measure frame recovery and CRC pass rate before adding higher-layer video or IP.

Known-good RF loopback baseline:

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --message "hello rf" --tx-gain-db -25 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --packet-count 100 --save-iq artifacts\baseline_100_ok.npz --plot-prefix artifacts\baseline_100_ok
```

Analyze a saved capture:

```powershell
python scripts/analyze_capture.py artifacts\baseline_100_ok.npz
```

Useful fields:

- `packets_ok` / `packet_error_rate`: packet recovery result.
- `theoretical_rrc_bandwidth_hz`: expected QPSK/RRC occupied bandwidth.
- `occupied_bw_99_hz` and `threshold_bw_minus_20db_hz`: measured spectrum width.
- `evm_rms_percent` and `snr_est_db`: rough constellation quality metrics.

15 Mbps raw QPSK payload test:

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --sample-rate 30000000 --symbol-rate 7500000 --bandwidth 20000000 --tx-gain-db -12 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --packet-count 100 --payload-pattern counter --payload-bytes 512 --save-iq artifacts\payload_512_15mbps.npz --plot-prefix artifacts\payload_512_15mbps
python scripts/analyze_capture.py artifacts\payload_512_15mbps.npz
```

For payload tests, `payload_bitrate_est_bps` is the estimated usable bitrate
after preamble, packet header, and CRC overhead. `payload_bitrate_ok_bps` also
accounts for packets that were actually recovered.
