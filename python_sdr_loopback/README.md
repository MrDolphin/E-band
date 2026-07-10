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
