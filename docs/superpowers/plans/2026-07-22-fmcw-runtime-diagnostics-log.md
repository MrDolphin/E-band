# FMCW Runtime Diagnostics Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add actionable E310 session, RX-signal, chirp-synchronization, and bounded-recovery diagnostics to the FMCW radar tab without changing radar thresholds or recovery policy.

**Architecture:** The synchronizer will attach measured values to `ChirpSyncError`; the E310 source will expose a thread-safe snapshot of the current radio session and raw RX level plus a bounded queue of short-lived phase transitions; the controller will combine those values with recovery counters in its immutable status. The GUI will render the latest snapshot and drain transition events into a deduplicated, bounded text log.

**Tech Stack:** Python 3.12, NumPy, Tkinter/ttk, `unittest`, existing pyadi-iio abstraction and fake-radio tests.

## Global Constraints

- This is one observability feature; do not alter correlation thresholds, gains, bandwidth, retry limits, settling delays, or cyclic-TX behavior.
- Do not access E310 during implementation or automated tests. Hardware verification is a separate Goal 4 activity after the user confirms the complete RF chain is restored.
- Preserve all untracked captures, `artifacts/`, caches, board-control material, and current unrelated working-tree changes.
- Keep UI history bounded to 200 rendered lines and avoid per-CPI log spam.
- Add structured values to existing classes; do not introduce a logging framework or external dependency.
- Keep new test code no larger than new production code where practical by extending existing fake-radio and parameterized test seams.

---

## File map

- Modify `python_sdr_loopback/sdr_loopback/radar/synchronizer.py`: structured synchronization-failure measurements.
- Modify `python_sdr_loopback/sdr_loopback/radar/sources.py`: session phase and latest raw-RX snapshot.
- Modify `python_sdr_loopback/sdr_loopback/radar/controller.py`: recovery counters and immutable combined status.
- Modify `python_sdr_loopback/scripts/sdr_video_gui.py`: FMCW diagnostics summary and bounded log panel.
- Modify `python_sdr_loopback/tests/test_radar_processor.py`: synchronization-error metric coverage.
- Modify `python_sdr_loopback/tests/test_radar_sources.py`: source-session and RX-level snapshot coverage.
- Modify `python_sdr_loopback/tests/test_radar_controller.py`: recovery progress and exhaustion coverage.
- Modify `python_sdr_loopback/tests/test_radar_ui.py`: rendering, deduplication, and bounded-history coverage.

### Task 1: Structured chirp synchronization failures

**Files:**
- Modify: `python_sdr_loopback/sdr_loopback/radar/synchronizer.py:11-98`
- Test: `python_sdr_loopback/tests/test_radar_processor.py:1-190`

**Interfaces:**
- Produces: `ChirpSyncDiagnostics` and `ChirpSyncError.diagnostics`.
- Consumed by: `RadarController._run()` in Task 3.

- [ ] **Step 1: Add a failing metric test**

Extend the existing correlation-mode test class with one deliberately invalid capture and assert that the exception includes the measured value and threshold:

```python
with self.assertRaises(ChirpSyncError) as caught:
    ChirpSynchronizer(config, mode="correlation").synchronize(capture)
diagnostics = caught.exception.diagnostics
self.assertEqual(diagnostics.failed_metric, "min_correlation")
self.assertLess(diagnostics.min_correlation, diagnostics.min_correlation_threshold)
self.assertIn("min_correlation=", str(caught.exception))
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```powershell
python -m unittest tests.test_radar_processor.ChirpSynchronizerTests -v
```

Expected: FAIL because `ChirpSyncError` has no `diagnostics` attribute.

- [ ] **Step 3: Add the structured error type**

Import `replace` with `dataclass`, then add an immutable value adjacent to `ChirpSyncResult`:

```python
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ChirpSyncDiagnostics:
    failed_metric: str
    min_correlation: float
    mean_correlation: float
    periodic_coherence: float | None
    idle_to_active_db: float
    min_correlation_threshold: float
    mean_correlation_threshold: float
    periodic_coherence_threshold: float


class ChirpSyncError(RuntimeError):
    def __init__(self, message: str, diagnostics: ChirpSyncDiagnostics | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics
```

When validation metrics are available, build one `ChirpSyncDiagnostics` and raise a formatted message such as:

```python
raise ChirpSyncError(
    f"chirp correlation is below the sync threshold "
    f"(min_correlation={correlation:.4f} < {self._MIN_CHIRP_CORRELATION:.4f})",
    replace(diagnostics, failed_metric="min_correlation"),
)
```

Keep existing messages for failures occurring before metrics exist and set `diagnostics=None`.

- [ ] **Step 4: Run the synchronizer tests**

Run the command from Step 2. Expected: all `ChirpSynchronizerTests` pass.

### Task 2: E310 session and raw-RX snapshot

**Files:**
- Modify: `python_sdr_loopback/sdr_loopback/radar/sources.py:29-289`
- Test: `python_sdr_loopback/tests/test_radar_sources.py:1-290`

**Interfaces:**
- Produces: `E310SourceDiagnostics`, `E310DiagnosticEvent`, `E310CpiSource.diagnostic_status()`, and `E310CpiSource.drain_diagnostic_events()`.
- Consumed by: `RadarController.status()` in Task 3.

- [ ] **Step 1: Add a failing fake-radio lifecycle test**

Extend the existing E310 fake-radio fixture to open, capture, recover, and inspect the snapshot:

```python
status = source.diagnostic_status()
self.assertEqual(status.session_attempt, 2)
self.assertEqual(status.phase, "capturing")
self.assertTrue(status.tx_uploaded)
self.assertEqual(status.rx_samples, capture.rx_iq.size)
self.assertTrue(math.isfinite(status.rx_rms_dbfs))
self.assertTrue(math.isfinite(status.rx_peak_dbfs))
self.assertGreaterEqual(status.clip_ratio, 0.0)
phases = [event.phase for event in source.drain_diagnostic_events()]
self.assertEqual(phases.count("connecting"), 2)
self.assertIn("recovering", phases)
self.assertIn("closing", phases)
self.assertIn("closed", phases)
self.assertEqual(phases[-1], "capturing")
```

- [ ] **Step 2: Run the source tests and confirm failure**

```powershell
python -m unittest tests.test_radar_sources.E310CpiSourceTests -v
```

Expected: FAIL because `diagnostic_status()` is absent.

- [ ] **Step 3: Implement the immutable source snapshot**

Add:

```python
@dataclass(frozen=True)
class E310SourceDiagnostics:
    session_attempt: int = 0
    phase: str = "closed"
    tx_uploaded: bool = False
    capture_count: int = 0
    rx_samples: int = 0
    rx_rms_dbfs: float = float("-inf")
    rx_peak_dbfs: float = float("-inf")
    clip_ratio: float = 0.0


@dataclass(frozen=True)
class E310DiagnosticEvent:
    timestamp: float
    session_attempt: int
    phase: str
    message: str
```

Maintain the snapshot and `collections.deque(maxlen=64)` event queue behind a dedicated lock. A private `_set_diagnostic_phase(phase, message)` updates both atomically. Record only meaningful boundaries: `connecting`, `alert`, `configuring`, `fdd`, `pre_tx_settle`, `tx_uploaded`, `discarding_startup_rx`, `capturing`, `recovering`, `closing`, `closed`, and `failed`. `drain_diagnostic_events()` returns and removes a tuple of queued events so fast ALERT/FDD transitions are not lost between 33 ms GUI polls.

In `capture()`, calculate normalized ADC metrics before returning `RadarCapture`:

```python
magnitude = np.abs(rx_iq)
rms = float(np.sqrt(np.mean(np.square(magnitude))))
peak = float(np.max(magnitude))
clip_ratio = float(np.mean(magnitude >= 1.0))
```

Convert RMS and peak with a local finite-safe dBFS helper. Increment `session_attempt` at the start of every `open()` attempt and keep the last failure phase observable until the next attempt.

- [ ] **Step 4: Run the focused source tests**

Run the command from Step 2. Expected: all E310 source tests pass without hardware.

### Task 3: Controller recovery status and exhaustion state

**Files:**
- Modify: `python_sdr_loopback/sdr_loopback/radar/controller.py:13-224`
- Test: `python_sdr_loopback/tests/test_radar_controller.py:130-290`

**Interfaces:**
- Consumes: `ChirpSyncError.diagnostics`, optional `source.diagnostic_status()`.
- Produces: additional immutable `ControllerStatus` fields and `drain_diagnostic_events()` used by the GUI.

- [ ] **Step 1: Extend the existing bounded-recovery test**

After driving repeated failures, assert:

```python
status = controller.status()
self.assertEqual(status.source_recoveries, 1)
self.assertEqual(status.max_source_recoveries, 1)
self.assertTrue(status.recovery_exhausted)
self.assertEqual(status.consecutive_processing_errors, 3)
self.assertIs(status.sync_diagnostics, sync_error.diagnostics)
```

Also assert that one later successful frame clears `recovery_exhausted`, resets both counters, and clears `sync_diagnostics`.

Add one compact case where `source.recover()` raises `RuntimeError("reopen failed")`; assert the controller stops, exposes that exact primary error, and still reports the last source phase as `failed`.

- [ ] **Step 2: Run the controller tests and confirm failure**

```powershell
python -m unittest tests.test_radar_controller.RadarControllerTests -v
```

Expected: FAIL because the new status fields are absent.

- [ ] **Step 3: Extend `ControllerStatus` and `_run()`**

Add fields:

```python
processing_error: str | None
consecutive_processing_errors: int
source_recoveries: int
max_source_recoveries: int
recovery_exhausted: bool
sync_diagnostics: object | None
source_diagnostics: object | None
```

Store counters under `_state_lock` whenever they change. Set `recovery_exhausted` only when another threshold crossing occurs after the configured maximum has been consumed. Do not stop the controller or change recovery policy. Obtain `source_diagnostics` with optional duck typing in `status()`:

```python
diagnostic_status = getattr(self.source, "diagnostic_status", None)
source_diagnostics = diagnostic_status() if diagnostic_status is not None else None
```

Keep `error` backward compatible, while exposing `processing_error` separately for the new UI.

Add `RadarController.drain_diagnostic_events()` as an optional-source adapter:

```python
def drain_diagnostic_events(self) -> tuple[object, ...]:
    drain = getattr(self.source, "drain_diagnostic_events", None)
    return tuple(drain()) if drain is not None else ()
```

- [ ] **Step 4: Run focused controller and source tests**

```powershell
python -m unittest tests.test_radar_controller tests.test_radar_sources -v
```

Expected: all tests pass.

### Task 4: FMCW diagnostics summary and bounded GUI log

**Files:**
- Modify: `python_sdr_loopback/scripts/sdr_video_gui.py:360-930`
- Test: `python_sdr_loopback/tests/test_radar_ui.py:600-760`

**Interfaces:**
- Consumes: extended `ControllerStatus` from Task 3.
- Produces: `format_radar_diagnostic_lines(status)`, `append_radar_log(lines)`, and a read-only `Text` log widget.

- [ ] **Step 1: Add formatter and deduplication tests**

Use `SimpleNamespace` snapshots so UI tests stay short. Assert the formatter includes:

```text
会话 3 | 阶段 capturing | TX循环 已上传
RX 249600点 | RMS -49.8 dBFS | 峰值 -38.6 dBFS | 削顶 0.000%
同步失败 min=0.0310/0.0500 mean=0.0620/0.0800 periodic=0.0040/0.0075
恢复 4/5 | 连续失败 16 | 正在重建会话
```

Call `append_radar_log()` twice with the same signature and assert only one line is inserted. Insert 205 unique transitions and assert the retained line count is 200.

- [ ] **Step 2: Run the UI tests and confirm failure**

```powershell
python -m unittest tests.test_radar_ui.RadarUiTests -v
```

Expected: FAIL because the formatter and log methods are absent.

- [ ] **Step 3: Add the diagnostics panel**

Under the current radar plots/table area, add a `ttk.LabelFrame(text="运行日志与同步诊断")` containing:

- one compact current-state label;
- a read-only `tk.Text(height=8, wrap=tk.NONE)`;
- a vertical scrollbar;
- `清空显示` and `保存日志` buttons.

`poll_radar_controller()` first drains every source phase event, then formats and appends only recovery/synchronization state changes large enough to be useful. Deduplicate state snapshots on this tuple:

```python
(
    session_attempt,
    phase,
    tx_uploaded,
    source_recoveries,
    recovery_exhausted,
    processing_error,
    failed_metric,
    round(min_correlation, 4),
    round(mean_correlation, 4),
    round(periodic_coherence or 0.0, 4),
)
```

Refresh the compact RX summary every poll without adding a history line for ordinary per-CPI RMS noise. `保存日志` writes UTF-8 text under a user-selected path; it must not automatically commit or place logs outside `artifacts/` by default.

- [ ] **Step 4: Run UI and impacted-module tests**

```powershell
python -m unittest tests.test_radar_ui tests.test_radar_controller tests.test_radar_sources tests.test_radar_processor -v
```

Expected: all impacted tests pass.

### Task 5: Review and one phase checkpoint

**Files:**
- Review only the eight files listed in the file map.

- [ ] **Step 1: Review scope and concurrency**

Confirm:

- GUI reads immutable snapshots only and never touches pyadi objects.
- No lock is held while calling `rx()`, `tx()`, `recover()`, Tkinter, or file dialogs.
- Log history remains bounded and repeated per-CPI values do not flood it.
- No gain, bandwidth, correlation threshold, retry count, or timing constant changed.
- Test additions remain proportionate to production changes.

- [ ] **Step 2: Run one full software regression**

```powershell
cd python_sdr_loopback
python -m unittest discover -s tests -v
```

Expected: all tests pass. Report only the final count and any failure summary, not the full log.

- [ ] **Step 3: Create one coherent phase commit**

Stage only the eight planned files, excluding `artifacts/`, caches, captures, binaries, and unrelated dirty files:

```powershell
git add -- python_sdr_loopback/sdr_loopback/radar/synchronizer.py python_sdr_loopback/sdr_loopback/radar/sources.py python_sdr_loopback/sdr_loopback/radar/controller.py python_sdr_loopback/scripts/sdr_video_gui.py python_sdr_loopback/tests/test_radar_processor.py python_sdr_loopback/tests/test_radar_sources.py python_sdr_loopback/tests/test_radar_controller.py python_sdr_loopback/tests/test_radar_ui.py
git commit -m "Add FMCW runtime synchronization diagnostics"
git push origin python-feature/fmcw-v0
```

- [ ] **Step 4: Stop before hardware testing**

Mark Goal 3 complete and wait. Goal 4 hardware verification must separately check:

1. whether the final cyclic TX remains present after recovery exhaustion;
2. whether the log reports a fatal `recover()` failure or a live session with stopped TX;
3. RX RMS/peak/clip levels with the complete TX/RX chain restored;
4. measured minimum/mean/periodic correlation against thresholds;
5. whether one successful synchronized CPI clears recovery exhaustion and allows background calibration.

Do not tune thresholds or retry timing until those measurements identify the failing boundary.
