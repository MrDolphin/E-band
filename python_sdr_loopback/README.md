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

At high sample rates, short frames have been most reliable with
`--tx-cyclic-copies 3`, matching the original 15 Mbps baseline. Long payload
frames are more sensitive; if TX silently fails, it shows up as `rx_rms_dbfs`
near -62 dBFS.
The default `--rx-discard-buffers 0` also matches that baseline.

For longer payload runs, keep each TX frame small and run multiple batches:

```powershell
python scripts/run_payload_batches.py --batches 20 --packets-per-batch 5 --payload-bytes 512 --tx-cyclic-copies 2 --artifact-prefix artifacts\payload_512_100_15mbps_batched
```

File transfer smoke test over the same RF loopback:

```powershell
python scripts/e310_rf_loopback.py --uri ip:192.168.1.10 --sample-rate 30000000 --symbol-rate 7500000 --bandwidth 20000000 --tx-gain-db -12 --tx-amplitude 0.5 --tx-dac-scale 8192 --rx-gain-db 10 --tx-channel 0 --rx-channel 0 --tx-port B --rx-port B_BALANCED --input-file artifacts\test_input.bin --output-file artifacts\test_output.bin --payload-bytes 512 --tx-cyclic-copies 3 --rx-discard-buffers 0 --min-rx-rms-dbfs -45 --rx-level-retries 8 --save-iq artifacts\file_transfer_15mbps.npz --plot-prefix artifacts\file_transfer_15mbps
```

Each 512-byte SDR payload carries a small file-fragment header plus file data.
Success is reported as `file_ok=true` after all chunks are recovered and the
whole-file CRC32 matches.

For larger images or small video files, avoid one huge cyclic TX buffer. Use
bounded batches and let the script concatenate verified parts:

```powershell
python scripts/run_file_transfer_batches.py --input-file phone.mp4 --output-file artifacts\recovered_phone.mp4 --batch-bytes 200000 --artifact-prefix artifacts\phone_batched
```

## FMCW radar MVP validation

The FMCW radar path accepts synthetic, replayed, and E310 IQ CPIs.  The normal
GUI configuration profiles deliberately separate the stable 20 MHz baseline
from 40 MHz validation and 56 MHz limit experiments; selecting a profile only
updates settings, and E310 is reconfigured on the next radar start.

The same deployable profiles are available to the CLI, so offline checks and a
later E310 session use identical waveform dimensions:

```powershell
python scripts\run_fmcw_radar.py --source synthetic --profile stable-20 --headless --metrics artifacts\fmcw_profiles\stable.jsonl
python scripts\run_fmcw_radar.py --source synthetic --profile validate-40 --headless --metrics artifacts\fmcw_profiles\validate.jsonl
python scripts\run_fmcw_radar.py --source e310 --profile limit-56 --dry-run
```

Explicit waveform arguments still override the selected profile for controlled
experiments. `--dry-run` never opens the radio.

With configured sweep bandwidth `B`, the software range-bin spacing is
`c / (2B)`: 7.49 m at 20 MHz and 2.68 m at 56 MHz.  Those are configured
baseband values.  Verify the effective RF sweep of the external multiplier
chain before claiming a different physical range resolution.

Create and analyse a deterministic capture:

```powershell
python scripts/generate_fmcw_fixture.py --output artifacts\fmcw_acceptance.npz --target 22.5,1.2,18 --seed 7
python scripts/analyze_fmcw_capture.py artifacts\fmcw_acceptance.npz --output-dir artifacts\fmcw_acceptance_result
```

Run a five-minute, headless synthetic soak.  It never opens E310 and writes
one diagnostics JSON object per CPI plus `soak_summary.json`:

```powershell
python scripts/run_fmcw_radar.py --source synthetic --duration-sec 300 --headless --metrics artifacts\fmcw_soak\metrics.jsonl
```

Success requires `frames_failed=0`, `queue_max_depth=1`,
`controller_stopped=true`, and `soak_ok=true`.  For a connected E310, inspect
startup synchronisation without transmitting or capturing when hardware is not
ready:

```powershell
python scripts/diagnose_fmcw_sync.py --dry-run --frames 10 --profile limit-56
```

On the current development PC, warmed synthetic 40 MHz and 56 MHz profiles
process one CPI in about 43 ms after bounded-CFAR optimization, while a 64-chirp
CPI lasts 9 ms.  The high-bandwidth profiles therefore validate configuration,
range resolution, and detection behavior, but the live controller deliberately
keeps only the newest frame rather than queueing every CPI indefinitely.
