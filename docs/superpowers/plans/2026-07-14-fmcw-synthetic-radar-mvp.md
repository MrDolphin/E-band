# E310 E-Band FMCW Radar MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested FMCW radar pipeline to `python_sdr_loopback` that generates or captures complex time-domain IQ, detects range/velocity/SNR from that IQ, and displays synchronized targets plus a semicircular radar and range-Doppler map in the existing Chinese GUI.

**Architecture:** Introduce a standalone `sdr_loopback.radar` package with immutable configuration and frame models, interchangeable synthetic/replay/E310 data sources, one shared signal-processing pipeline, and a latest-frame controller. Keep FFT/CFAR and hardware control out of `scripts/sdr_video_gui.py`; the Tkinter layer only binds controls and renders a complete `RadarFrame`.

**Tech Stack:** Python 3.12, NumPy, pyadi-iio, Tkinter, Pillow, standard-library `unittest`, NPZ/JSON/JSONL files.

## Global Constraints

- Work only in `D:\hp-laptop\E-band-fmcw` on branch `python-feature/fmcw-v0`.
- Preserve untracked `python_sdr_loopback/artifacts/` and `__pycache__/` directories; never stage them.
- Default radar values are fixed at 76 GHz carrier, 30 MSPS complex IQ, 20 MHz sweep, 128 us active time, 16 us idle time, 64 chirps, 3840 active samples, 4320 total samples/chirp, and 4096-point range FFT.
- `RadarConfig` is the single source of truth for all derived physics shown by CLI or GUI.
- Synthetic, replay, and E310 data must enter the same `FmcwProcessor`; no source may manufacture detections or a heatmap.
- The first receiver is single-channel. Set `azimuth_deg=None`; render detections on the 0-degree reference line and label angle as unmeasured.
- Use NumPy-only signal processing. Do not add SciPy solely for filtering, interpolation, peak finding, or CFAR.
- Keep finite-CPI acquisition. Do not promise continuous 30 MSPS host streaming.
- Never run direct TX/RX coax loopback with only 6 dB attenuation.
- Run the focused test after every red/green step, then the full Python test suite before each commit.
- Create one commit per completed task and push it after verification.

## Planned File Structure

```text
python_sdr_loopback/
  sdr_loopback/radar/
    __init__.py
    config.py
    models.py
    waveform.py
    simulator.py
    synchronizer.py
    processor.py
    detector.py
    storage.py
    sources.py
    calibration.py
    controller.py
    ui.py
  scripts/
    run_fmcw_radar.py
    analyze_fmcw_capture.py
    generate_fmcw_fixture.py
    sdr_video_gui.py
  tests/
    test_radar_config.py
    test_radar_waveform.py
    test_radar_simulator.py
    test_radar_processor.py
    test_radar_detector.py
    test_radar_storage.py
    test_radar_controller.py
    test_radar_sources.py
    test_radar_cli.py
    test_radar_ui.py
    test_radar_performance.py
```

---

## Task 1: Establish Radar Configuration and Frame Contracts

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/__init__.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/config.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/models.py`
- Create: `python_sdr_loopback/tests/test_radar_config.py`

- [ ] **Step 1: Write failing configuration and model tests**

```python
class RadarConfigTests(unittest.TestCase):
    def test_default_dimensions_and_physics(self):
        config = RadarConfig()
        self.assertEqual(config.active_samples, 3840)
        self.assertEqual(config.samples_per_chirp, 4320)
        self.assertEqual(config.cpi_samples, 276480)
        self.assertEqual(config.range_fft_size, 4096)
        self.assertAlmostEqual(config.range_resolution_m, 7.49481145, places=5)
        self.assertAlmostEqual(config.max_unambiguous_velocity_mps, 6.85, delta=0.03)
        self.assertAlmostEqual(config.velocity_resolution_mps, 0.214, delta=0.01)

    def test_non_integral_sample_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer sample count"):
            RadarConfig(active_time_s=128.01e-6)

    def test_single_rx_target_has_no_angle(self):
        target = RadarTarget("T01", 22.5, 1.2, None, 18.0, -12.0, 3, 38, 0.9, 1.0)
        self.assertIsNone(target.azimuth_deg)
```

- [ ] **Step 2: Run the focused test and confirm it fails because the radar package does not exist**

Run: `python -m unittest tests.test_radar_config -v` from `python_sdr_loopback`

Expected: `ModuleNotFoundError: No module named 'sdr_loopback.radar'`.

- [ ] **Step 3: Implement immutable configuration, validation, derived properties, and frame dataclasses**

```python
SPEED_OF_LIGHT_MPS = 299_792_458.0

@dataclass(frozen=True)
class RadarConfig:
    carrier_hz: float = 76e9
    sample_rate_hz: float = 30e6
    bandwidth_hz: float = 20e6
    active_time_s: float = 128e-6
    idle_time_s: float = 16e-6
    chirp_count: int = 64
    range_fft_size: int = 4096
    doppler_fft_size: int = 64
    max_display_range_m: float = 50.0
    cfar_threshold_db: float = 12.0

    def __post_init__(self) -> None:
        active = self.sample_rate_hz * self.active_time_s
        total = self.sample_rate_hz * self.chirp_period_s
        if not math.isclose(active, round(active), abs_tol=1e-9):
            raise ValueError("active_time_s must produce an integer sample count")
        if not math.isclose(total, round(total), abs_tol=1e-9):
            raise ValueError("chirp period must produce an integer sample count")
        if self.range_fft_size < round(active):
            raise ValueError("range_fft_size must cover all active samples")

    @property
    def chirp_period_s(self) -> float:
        return self.active_time_s + self.idle_time_s

    @property
    def active_samples(self) -> int:
        return round(self.sample_rate_hz * self.active_time_s)

    @property
    def samples_per_chirp(self) -> int:
        return round(self.sample_rate_hz * self.chirp_period_s)
```

Define frozen `SyntheticTarget`, `RadarTarget`, `RadarDiagnostics`, `RadarCapture`, and `RadarFrame`. Array-bearing models validate shapes but do not use dataclass-generated equality for NumPy arrays.

- [ ] **Step 4: Run focused and full tests**

Run: `python -m unittest tests.test_radar_config -v`

Expected: all configuration/model tests pass.

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: `OK`.

- [ ] **Step 5: Commit and push**

```powershell
git add python_sdr_loopback/sdr_loopback/radar/__init__.py python_sdr_loopback/sdr_loopback/radar/config.py python_sdr_loopback/sdr_loopback/radar/models.py python_sdr_loopback/tests/test_radar_config.py
git commit -m "Add FMCW radar configuration models"
git push origin python-feature/fmcw-v0
```

---

## Task 2: Generate Reference FMCW Waveforms and Time-Domain Synthetic IQ

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/waveform.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/simulator.py`
- Create: `python_sdr_loopback/tests/test_radar_waveform.py`
- Create: `python_sdr_loopback/tests/test_radar_simulator.py`

- [ ] **Step 1: Write failing waveform tests**

Verify the active chirp has 3840 unit-magnitude complex samples, the idle region is zero, the CPI has 276480 samples, and unwrapped phase differences span the configured 20 MHz sweep without crossing complex-baseband Nyquist.

```python
chirp = generate_chirp(RadarConfig())
self.assertEqual(chirp.shape, (4320,))
np.testing.assert_allclose(np.abs(chirp[:3840]), 1.0, atol=1e-6)
np.testing.assert_array_equal(chirp[3840:], 0.0)
self.assertEqual(generate_cpi(RadarConfig()).shape, (276480,))
```

- [ ] **Step 2: Run the waveform test and observe the missing module failure**

Run: `python -m unittest tests.test_radar_waveform -v`

Expected: import failure for `radar.waveform`.

- [ ] **Step 3: Implement phase-continuous complex chirp generation**

Use a baseband sweep from `-bandwidth_hz / 2` to `+bandwidth_hz / 2`:

```python
def generate_active_chirp(config: RadarConfig) -> np.ndarray:
    t = np.arange(config.active_samples, dtype=np.float64) / config.sample_rate_hz
    f0 = -0.5 * config.bandwidth_hz
    phase = 2.0 * np.pi * (f0 * t + 0.5 * config.chirp_slope_hz_per_s * t * t)
    return np.exp(1j * phase).astype(np.complex64)
```

Append an exact zero-valued idle segment and tile exactly `chirp_count` times for the CPI.

- [ ] **Step 4: Write failing simulator tests for fractional delay, Doppler, deterministic noise, leakage, and multiple targets**

Use a small test configuration to keep tests fast. Validate a 22.5 m target is not rounded to an integer delay, a positive velocity produces the expected slow-time phase direction, identical seeds produce identical IQ, and truth metadata is stored only on `RadarCapture`.

- [ ] **Step 5: Implement fractional-delay simulation without SciPy**

Generate the delayed active chirp by evaluating chirp phase at `t - 2R/c`, mask samples outside the active interval, apply continuous Doppler phase over absolute CPI time, then add leakage, clutter, and complex Gaussian noise:

```python
delay_s = 2.0 * target.range_m / SPEED_OF_LIGHT_MPS
delayed_t = fast_time - delay_s
valid = (delayed_t >= 0.0) & (delayed_t < config.active_time_s)
echo[valid] = chirp_at(delayed_t[valid], config)
echo *= np.exp(1j * 2.0 * np.pi * target.doppler_hz(config) * absolute_time)
```

Scale noise from aggregate target power and requested SNR. Do not pass truth targets into processor inputs.

- [ ] **Step 6: Run simulator tests and the full suite**

Run: `python -m unittest tests.test_radar_waveform tests.test_radar_simulator -v`

Expected: all waveform and simulation scenarios pass.

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: `OK`.

- [ ] **Step 7: Commit and push**

```powershell
git add python_sdr_loopback/sdr_loopback/radar/waveform.py python_sdr_loopback/sdr_loopback/radar/simulator.py python_sdr_loopback/tests/test_radar_waveform.py python_sdr_loopback/tests/test_radar_simulator.py
git commit -m "Add FMCW waveform and target simulator"
git push origin python-feature/fmcw-v0
```

---

## Task 3: Synchronize Chirps and Build the Range-Doppler Processor

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/synchronizer.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/processor.py`
- Create: `python_sdr_loopback/tests/test_radar_processor.py`

- [ ] **Step 1: Write failing tests for aligned, offset, incomplete, and non-finite captures**

Assert that an inserted leading offset is recovered within one sample, exactly 64 complete chirps are returned, truncated captures raise `ChirpSyncError`, and NaN/Inf input is rejected before FFT.

- [ ] **Step 2: Implement synchronization with two explicit modes**

`known` mode uses capture metadata for synthetic/replay fixtures. `correlation` mode searches a bounded prefix using normalized correlation against the first active reference chirp and validates subsequent idle energy and chirp spacing.

```python
@dataclass(frozen=True)
class ChirpSyncResult:
    start_sample: int
    chirp_starts: np.ndarray
    correlation: float
    idle_to_active_db: float
```

- [ ] **Step 3: Write failing range-Doppler tests before implementing the processor**

```python
frame = FmcwProcessor(config).process(capture)
peak = np.unravel_index(np.argmax(frame.range_doppler_db), frame.range_doppler_db.shape)
self.assertLessEqual(abs(frame.range_axis_m[peak[1]] - truth.range_m), config.range_resolution_m)
self.assertLessEqual(abs(frame.velocity_axis_mps[peak[0]] - truth.radial_velocity_mps), config.velocity_resolution_mps)
self.assertEqual(frame.range_doppler_db.shape, (64, 2049))
```

- [ ] **Step 4: Implement dechirp, windows, FFTs, physical axes, and diagnostics**

```python
beat = chirps * np.conj(reference_active[None, :])
beat -= np.mean(beat, axis=1, keepdims=True)
windowed = beat * np.hanning(config.active_samples)
full_range = np.fft.fft(windowed, n=config.range_fft_size, axis=1)
# Delayed echoes yield negative beat frequency under rx * conj(tx).
# Reorder DC and the negative half so columns increase with positive range.
range_spectrum = np.concatenate(
    (full_range[:, :1], full_range[:, : config.range_fft_size // 2 - 1 : -1]),
    axis=1,
)
range_spectrum -= np.mean(range_spectrum, axis=0, keepdims=True)
rd = np.fft.fftshift(
    np.fft.fft(range_spectrum * np.hanning(config.chirp_count)[:, None], n=config.doppler_fft_size, axis=0),
    axes=0,
)
```

Do not call `np.fft.rfft` on complex beat data. Build range from beat frequency/chirp slope and velocity from Doppler frequency/wavelength. Record processing time, RX RMS/peak dBFS, clip ratio, sync score, noise floor, and phase-consistency score.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m unittest tests.test_radar_processor -v`

Expected: synchronization, axis, and peak-localization tests pass.

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: `OK`.

- [ ] **Step 6: Commit and push**

```powershell
git add python_sdr_loopback/sdr_loopback/radar/synchronizer.py python_sdr_loopback/sdr_loopback/radar/processor.py python_sdr_loopback/tests/test_radar_processor.py
git commit -m "Add FMCW range Doppler processing"
git push origin python-feature/fmcw-v0
```

---

## Task 4: Detect and Cluster Targets with 2D CA-CFAR

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/detector.py`
- Create: `python_sdr_loopback/tests/test_radar_detector.py`
- Modify: `python_sdr_loopback/sdr_loopback/radar/processor.py`
- Modify: `python_sdr_loopback/sdr_loopback/radar/models.py`

- [ ] **Step 1: Write failing detector tests**

Cover single target, two separated targets, same range/different velocity, weak target, pure noise, and strong zero-range leakage plus a distant target. Acceptance: distance error <= one physical range-resolution cell; velocity error <= one physical Doppler cell; controlled-fixture SNR error <= 3 dB; ten seeded noise CPIs produce no persistent target; every target has `azimuth_deg is None`.

- [ ] **Step 2: Implement NumPy-only 2D CA-CFAR**

Use linear power, an integral image for rectangular training sums, explicit guard removal, and edge exclusion. Return a boolean mask and local noise power. Initial Doppler training/guard is `4/1`; range training/guard is `8/2`.

```python
threshold = noise_power * (10.0 ** (config.cfar_threshold_db / 10.0))
detections = power > threshold
```

- [ ] **Step 3: Implement local maxima and deterministic connected clustering**

Cluster 8-connected cells, retain maximum power per cluster, sort by descending SNR, assign frame-local IDs `T01`, `T02`, and compute confidence from CFAR margin plus sync/phase diagnostics.

- [ ] **Step 4: Integrate detection into `FmcwProcessor.process`**

One call returns one complete `RadarFrame` containing axes, dB matrix, target tuple, and diagnostics with one `frame_index`.

- [ ] **Step 5: Run tests, commit, and push**

Run: `python -m unittest tests.test_radar_detector -v`

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: all tests pass and full suite reports `OK`.

```powershell
git add python_sdr_loopback/sdr_loopback/radar/detector.py python_sdr_loopback/sdr_loopback/radar/processor.py python_sdr_loopback/sdr_loopback/radar/models.py python_sdr_loopback/tests/test_radar_detector.py
git commit -m "Add FMCW CFAR target detection"
git push origin python-feature/fmcw-v0
```

---

## Task 5: Add Capture Storage, Replay, Fixtures, and CLI Analysis

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/storage.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/sources.py`
- Create: `python_sdr_loopback/scripts/generate_fmcw_fixture.py`
- Create: `python_sdr_loopback/scripts/analyze_fmcw_capture.py`
- Create: `python_sdr_loopback/scripts/run_fmcw_radar.py`
- Create: `python_sdr_loopback/tests/test_radar_storage.py`
- Create: `python_sdr_loopback/tests/test_radar_cli.py`

- [ ] **Step 1: Write failing NPZ/JSON round-trip tests**

Assert complex64 `tx_iq`/`rx_iq`, `radar_config_json`, optional `truth_targets_json`, and timestamp survive NPZ round trip. Assert `result.json`, `range_doppler.npy`, and one JSON object per `metrics.jsonl` line load independently.

- [ ] **Step 2: Implement strict storage functions**

```python
def save_capture(path: Path, capture: RadarCapture) -> None:
    np.savez_compressed(
        path,
        tx_iq=capture.tx_iq.astype(np.complex64),
        rx_iq=capture.rx_iq.astype(np.complex64),
        radar_config_json=capture.config.to_json(),
        truth_targets_json=json.dumps([item.to_dict() for item in capture.truth_targets]),
        capture_timestamp=capture.timestamp,
    )

def load_capture(path: Path) -> RadarCapture:
    with np.load(path, allow_pickle=False) as data:
        return RadarCapture.from_serialized_fields(data)
```

`save_frame` writes `result.json` and `range_doppler.npy`; `append_metrics` writes exactly one serialized diagnostics object followed by `\n`.

Reject missing fields, mismatched IQ lengths, invalid JSON, and config/sample-count mismatches with specific `ValueError` messages.

- [ ] **Step 3: Define the common source protocol and implement synthetic/replay sources**

```python
class RadarDataSource(Protocol):
    def open(self) -> None:
        raise NotImplementedError

    def capture(self) -> RadarCapture:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
```

`SyntheticTargetSource` uses an incrementing seed/frame counter. `IqReplaySource` loads captures in order and loops only when explicitly requested.

- [ ] **Step 4: Implement fixture and analysis CLIs with machine-readable output**

```powershell
python scripts\generate_fmcw_fixture.py --output artifacts\fmcw_single_target.npz --target 22.5,1.2,18 --seed 7
python scripts\analyze_fmcw_capture.py artifacts\fmcw_single_target.npz --output-dir artifacts\fmcw_single_result
```

Expected key output:

```text
active_samples=3840
chirp_count=64
truth_targets=1
targets_detected=1
azimuth_measured=false
```

Detected range/velocity must be within one physical bin of 22.5 m/+1.2 m/s.

- [ ] **Step 5: Run subprocess tests and the full suite**

Use reduced test configuration flags so tests finish quickly without changing production defaults.

Run: `python -m unittest tests.test_radar_storage tests.test_radar_cli -v`

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: round-trip and CLI workflows pass; full suite reports `OK`.

- [ ] **Step 6: Commit and push**

```powershell
git add python_sdr_loopback/sdr_loopback/radar/storage.py python_sdr_loopback/sdr_loopback/radar/sources.py python_sdr_loopback/scripts/generate_fmcw_fixture.py python_sdr_loopback/scripts/analyze_fmcw_capture.py python_sdr_loopback/scripts/run_fmcw_radar.py python_sdr_loopback/tests/test_radar_storage.py python_sdr_loopback/tests/test_radar_cli.py
git commit -m "Add FMCW capture and analysis tools"
git push origin python-feature/fmcw-v0
```

---

## Task 6: Add a Latest-Frame Radar Controller

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/controller.py`
- Create: `python_sdr_loopback/tests/test_radar_controller.py`

- [ ] **Step 1: Write failing lifecycle and backpressure tests**

Use fake source/processor objects. Assert `start()` opens once, worker exceptions become status, `stop()` always closes, and publishing three frames into a full queue leaves only the newest frame.

```python
controller.publish(frame1)
controller.publish(frame2)
controller.publish(frame3)
self.assertEqual(controller.latest_frame().frame_index, 3)
self.assertEqual(controller.dropped_display_frames, 2)
```

- [ ] **Step 2: Implement a daemon worker with `queue.Queue(maxsize=1)`**

The worker owns source open/capture/close and processing. `publish` removes one stale item before non-blocking put. GUI methods return immutable status snapshots; no Tkinter call occurs in the worker.

- [ ] **Step 3: Add timeout, overrun, and clean shutdown behavior**

Track acquisition/processing durations and dropped display frames. Stop waits at most two CPI periods plus one second, then reports shutdown error while still closing the source.

- [ ] **Step 4: Run tests, commit, and push**

Run: `python -m unittest tests.test_radar_controller -v`

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: lifecycle/backpressure tests pass; full suite reports `OK`.

```powershell
git add python_sdr_loopback/sdr_loopback/radar/controller.py python_sdr_loopback/tests/test_radar_controller.py
git commit -m "Add FMCW radar runtime controller"
git push origin python-feature/fmcw-v0
```

---

## Task 7: Add the FMCW Configuration Panel and Radar Dashboard

**Files:**
- Create: `python_sdr_loopback/sdr_loopback/radar/ui.py`
- Create: `python_sdr_loopback/tests/test_radar_ui.py`
- Modify: `python_sdr_loopback/scripts/sdr_video_gui.py`

- [ ] **Step 1: Extract pure display transforms and write tests first**

Test `radar_xy`, heatmap downsampling, axis ticks, target-row formatting, and Chinese diagnostics without creating a Tk root.

```python
x, y = radar_xy(25.0, None, 200, 50.0)
self.assertAlmostEqual(x, 0.0)
self.assertAlmostEqual(y, -100.0)
self.assertIn("方位角未测量", format_target(target))
```

- [ ] **Step 2: Implement reusable panes in `radar/ui.py`**

- `SemicircleRadarPane`: upper semicircle; 10/20/30/40/50 m rings; -90/-60/-30/0/+30/+60/+90 degree ticks; labeled azimuth and radial distance axes.
- `RangeDopplerPane`: Canvas bitmap heatmap with explicit range/velocity axes and at least 64 x 32 visible cells.
- `TargetTablePane`: ID, range, radial velocity, angle status, SNR, confidence.
- `RadarDiagnosticsPane`: frame, source, sync, phase consistency, RMS, peak, clipping, noise floor, processing time, overruns.

Redraw every pane from one `RadarFrame` only.

- [ ] **Step 3: Add GUI variables, FMCW settings, and the third tab**

Add source mode, carrier, sample rate, bandwidth, active/idle times, chirp count, gains/channels, CFAR threshold, display range, replay path, and synthetic target variables.

```python
self.radar_tab = ttk.Frame(self.notebook, padding=14)
self.notebook.add(self.radar_tab, text="FMCW雷达")
```

Add `FMCW雷达配置` below video configuration. Display derived values by constructing `RadarConfig`; do not duplicate formulas in GUI code.

- [ ] **Step 4: Bind start/stop/save and poll the controller**

Validate fields, build selected source/processor, start controller, and switch tabs. Poll every 33 ms; render only when `frame_index` changes. Window close stops video and radar before destroy.

- [ ] **Step 5: Run automated and compilation checks**

Run: `python -m unittest tests.test_radar_ui tests.test_radar_controller -v`

Run: `python -m py_compile scripts\sdr_video_gui.py sdr_loopback\radar\ui.py`

Expected: tests pass; compilation exits 0 with no output.

- [ ] **Step 6: Run manual synthetic GUI acceptance**

Run: `python scripts\sdr_video_gui.py`

Verify Simplified Chinese labels; default 3840 samples/7.5 m/about +/-6.85 m/s/about 0.21 m/s/9.216 ms values; standard semicircle and labeled axes; range-Doppler grid >=64 x 32; one frame ID across all views; visible `单RX：方位角未测量`; clean stop/close.

- [ ] **Step 7: Run full tests, commit, and push**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: `OK`.

```powershell
git add python_sdr_loopback/sdr_loopback/radar/ui.py python_sdr_loopback/scripts/sdr_video_gui.py python_sdr_loopback/tests/test_radar_ui.py
git commit -m "Add FMCW radar dashboard"
git push origin python-feature/fmcw-v0
```

---

## Task 8: Add Finite-CPI E310 Acquisition and Empty-Room Calibration

**Files:**
- Modify: `python_sdr_loopback/sdr_loopback/radar/sources.py`
- Create: `python_sdr_loopback/sdr_loopback/radar/calibration.py`
- Create: `python_sdr_loopback/tests/test_radar_sources.py`
- Modify: `python_sdr_loopback/tests/test_radar_processor.py`
- Modify: `python_sdr_loopback/scripts/run_fmcw_radar.py`

- [ ] **Step 1: Write fake-ADI tests for configuration, acquisition, cleanup, and errors**

Verify URI `ip:192.168.1.10`, AD9361 LO 900 MHz, 30 MSPS, 20 MHz analog bandwidth, selected ports/channels, manual gain, cyclic TX, and RX buffer >= one CPI. Assert both buffers are destroyed on success, IIO exception, and close.

- [ ] **Step 2: Implement `E310CpiSource` with dependency injection**

`E310CpiSource(config: RadarConfig, radio_config: E310RadioConfig, adi_module: object | None = None)` implements the same `open()`, `capture() -> RadarCapture`, and `close()` contract as synthetic/replay sources. `open()` configures AD9361 and uploads TX; `capture()` obtains and validates one finite CPI; `close()` destroys TX/RX buffers and clears the context reference.

Scale normalized chirps to DAC counts without clipping, upload a finite repeated CPI to cyclic TX, settle briefly, capture synchronization margin plus one CPI, and return complex64 IQ. Reproduce the proven buffer cleanup from `scripts/e310_rf_loopback.py` inside the source rather than importing script functions.

- [ ] **Step 3: Implement empty-room background calibration**

Accumulate aligned dechirped matrices from configurable empty-room CPIs, store complex mean plus metadata, subtract only when configuration hashes match, and apply a configurable near-range guard. Reject mismatched calibration explicitly.

- [ ] **Step 4: Add phase-consistency trust handling**

When adjacent-chirp correlation or phase consistency is below threshold, retain range but set velocity confidence to zero and `velocity_trusted=False`. GUI displays `速度不可信` rather than a precise result.

- [ ] **Step 5: Add E310 CLI dry-run**

Run: `python scripts\run_fmcw_radar.py --source e310 --dry-run`

Expected:

```text
source=e310
uri=ip:192.168.1.10
sample_rate_hz=30000000
cpi_samples=276480
hardware_access=false
```

- [ ] **Step 6: Run automated tests**

Run: `python -m unittest tests.test_radar_sources tests.test_radar_processor -v`

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: fake hardware, calibration, trust tests pass; full suite reports `OK`.

- [ ] **Step 7: Perform device-side check with spatially separated horns**

Use low TX gain and no coax direct loopback. Capture empty-room calibration, then add a metal reflector. Verify clip ratio near zero, 64 chirps synchronize, and a repeatable new range peak appears. Accept velocity only after phase diagnostics mark it trusted.

- [ ] **Step 8: Commit and push only after automated and device-side checks pass**

```powershell
git add python_sdr_loopback/sdr_loopback/radar/sources.py python_sdr_loopback/sdr_loopback/radar/calibration.py python_sdr_loopback/scripts/run_fmcw_radar.py python_sdr_loopback/tests/test_radar_sources.py python_sdr_loopback/tests/test_radar_processor.py
git commit -m "Add E310 FMCW CPI acquisition"
git push origin python-feature/fmcw-v0
```

If hardware verification cannot run, do not commit Task 8; report verified fake-ADI changes and the exact remaining device check.

---

## Task 9: Complete Performance, Soak, and User Documentation

**Files:**
- Modify: `python_sdr_loopback/README.md`
- Modify: `python_sdr_loopback/README.zh-CN.md`
- Modify: `python_sdr_loopback/scripts/run_fmcw_radar.py`
- Modify: `python_sdr_loopback/sdr_loopback/radar/processor.py`
- Create: `python_sdr_loopback/tests/test_radar_performance.py`

- [ ] **Step 1: Add a default-config performance test**

Warm once, process five seeded default CPIs, and assert median processing time below 50 ms on the development PC. Skip only when NumPy is unavailable, never because processing is slow.

- [ ] **Step 2: Add five-minute synthetic soak mode**

```powershell
python scripts\run_fmcw_radar.py --source synthetic --duration-sec 300 --headless --metrics artifacts\fmcw_soak\metrics.jsonl
```

Expected final output:

```text
frames_failed=0
queue_max_depth=1
controller_stopped=true
soak_ok=true
```

- [ ] **Step 3: Document formulas, limitations, commands, outputs, and hardware safety in English and Simplified Chinese**

Document 3840/4320/4096/64 dimensions, finite-CPI memory cost, real versus complex sampling, range/velocity formulas, single-RX angle limitation, fixture/analyze/GUI commands, output files, and the 6 dB direct-loopback prohibition.

- [ ] **Step 4: Run final verification**

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -m py_compile scripts\sdr_video_gui.py scripts\run_fmcw_radar.py scripts\analyze_fmcw_capture.py scripts\generate_fmcw_fixture.py
python scripts\generate_fmcw_fixture.py --output artifacts\fmcw_acceptance.npz --target 22.5,1.2,18 --seed 7
python scripts\analyze_fmcw_capture.py artifacts\fmcw_acceptance.npz --output-dir artifacts\fmcw_acceptance_result
```

Expected: tests report `OK`, compilation has no output, fixture succeeds, and analysis recovers range/velocity within one physical bin with no measured azimuth.

- [ ] **Step 5: Run manual GUI soak and inspect artifacts**

Run `python scripts\sdr_video_gui.py` in synthetic mode for five minutes. Confirm responsiveness, synchronized frame IDs, no growing lag, readable Chinese labels, and valid `result.json`, `range_doppler.npy`, and `metrics.jsonl`.

- [ ] **Step 6: Commit and push**

```powershell
git add python_sdr_loopback/README.md python_sdr_loopback/README.zh-CN.md python_sdr_loopback/scripts/run_fmcw_radar.py python_sdr_loopback/sdr_loopback/radar/processor.py python_sdr_loopback/tests/test_radar_performance.py
git commit -m "Document and validate FMCW radar MVP"
git push origin python-feature/fmcw-v0
```

---

## Final Acceptance Checklist

- [ ] All automated tests pass from `python_sdr_loopback`.
- [ ] Dimensions are exactly 3840 active, 4320 per chirp, 64 chirps, and 4096 range FFT.
- [ ] Synthetic truth enters only the time-domain simulator; processor/GUI never consume truth.
- [ ] Distance, velocity, and SNR meet agreed error bounds.
- [ ] Pure-noise and leakage fixtures do not create persistent false targets.
- [ ] Radar, heatmap, target table, and diagnostics use one `frame_index`.
- [ ] GUI uses a standard semicircular radar and labeled range/velocity axes.
- [ ] Single-RX angle is visibly unmeasured everywhere.
- [ ] Latest-frame queue depth never exceeds one.
- [ ] Default CPI processing median is below 50 ms on the development PC.
- [ ] Five-minute synthetic GUI soak finishes without freeze or growing delay.
- [ ] E310 cleanup is proven by fake-ADI tests and confirmed on hardware before Task 8 commit.
- [ ] English and Simplified Chinese README commands match actual behavior.
