# FMCW Task 3 Implementation Report

## Status

Complete. Chirp synchronization and the shared NumPy range-Doppler processor
are implemented over the Task 1-2 time-domain IQ contracts.

## Implemented

- Added `ChirpSynchronizer` with explicit `known` and `correlation` modes.
- Added `ChirpSyncResult` and `ChirpSyncError`.
- Correlation mode searches only a bounded prefix of measured RX IQ using
  normalized correlation, then validates all 64 chirps at the configured
  spacing and checks idle-to-active energy.
- Both synchronization modes reject incomplete and non-finite captures before
  FFT processing.
- Added `FmcwProcessor` with `rx * conj(tx_reference)` dechirping, fast-time and
  slow-time Hann windows, range and Doppler FFTs, and mean clutter removal.
- Preserved the delayed-up-chirp sign convention by mapping DC followed by the
  reversed negative FFT half to increasing positive range.
- Built physical range and velocity axes from chirp slope, sample rate,
  wavelength, and chirp period. Positive synthetic radial velocity remains on
  the positive velocity axis.
- Changed `RadarFrame` validation to the nonnegative physical range
  half-spectrum: `(doppler_fft_size, range_fft_size // 2 + 1)`.
- Processor output intentionally returns `targets=()` pending detector
  integration and never reads `capture.truth_targets`.
- Populated every existing `RadarDiagnostics` field from synchronization,
  measured RX IQ, range-Doppler data, or measured processing time.
- Exported the new synchronization and processing APIs from `radar.__init__`.

## TDD Evidence

The processor test module and half-spectrum model expectation were written
first. The initial focused run failed because `processor.py` and
`synchronizer.py` did not exist, and the model test failed because the legacy
`RadarFrame` contract still required 4096 range bins.

After the first green run, self-review identified that correlation must search
measured RX rather than the transmit reference itself. The offset fixture was
tightened so only RX carried the 37-sample prefix; it failed on the old
same-length/transmit-search behavior, then passed after the synchronization fix.

## Verification

Focused command:

```text
python -m unittest tests.test_radar_processor -v
Ran 7 tests - OK
```

Full command:

```text
python -m unittest discover -s tests -p "test_*.py" -v
Ran 21 tests - OK
```

Coverage includes aligned known synchronization, correlation offset recovery,
64 complete chirp starts, incomplete capture errors, NaN/Inf rejection, range
and positive-velocity localization, half-spectrum dimensions, measured
diagnostics, and protection against reading truth targets.

## Scope And Preservation

Only the assigned radar implementation, exports, model shape validation, tests,
and this requested report were changed. `waveform.py`, `simulator.py`, unrelated
files, existing artifact/cache directories, and the tracked
`.superpowers/sdd/task-2-report.md` were not modified.

## Concern

The existing immutable `RadarDiagnostics` contract exposes `sync_ok` and
`clipping` as booleans, not numeric synchronization-score and clipping-ratio
fields. Per the ownership restriction, the model was changed only for the
required half-spectrum shape. The processor computes correlation and clip ratio
from measured IQ, but the current contract can expose only their boolean status;
adding numeric fields belongs in a separately owned contract change.

## Follow-Up Diagnostics Fix Evidence

The diagnostics-contract concern above is resolved before review.
`RadarDiagnostics` now retains the existing `sync_ok` and `clipping` status
booleans and additionally exposes numeric `sync_score: float` and
`clip_ratio: float` measurements.

The focused test was changed first to require the synchronizer's measured
correlation and a deliberately nonzero ratio from 17 clipped samples. Before
the model change, the targeted test failed with:

```text
AttributeError: 'RadarDiagnostics' object has no attribute 'sync_score'
```

`FmcwProcessor` now assigns `sync.correlation` directly to `sync_score` and
the measured fraction of RX samples whose magnitude is at least full scale to
`clip_ratio`. The booleans continue to report successful synchronization and
whether the numeric clipping ratio is nonzero.

Post-fix verification:

```text
python -m unittest tests.test_radar_processor -v
Ran 7 tests - OK

python -m unittest discover -s tests -p "test_*.py" -v
Ran 21 tests - OK
```

No Task 3 implementation concerns remain from this contract mismatch.

## Task 3 Review Findings Fix

All three review findings were resolved with tests-first coverage.

1. `RadarCapture` now carries validated `chirp_start_sample` metadata. TX IQ
   remains exactly one CPI, while RX IQ may include a complex one-dimensional
   synchronization prefix or trailing margin as long as the metadata-selected
   CPI is complete. Known synchronization receives this metadata explicitly,
   and `FmcwProcessor` slices exactly that one CPI for FFTs and diagnostics.
2. `RadarConfig` now requires positive power-of-two range and Doppler FFT sizes,
   with range FFT capacity at least `active_samples` and Doppler FFT capacity at
   least `chirp_count`. `RadarFrame` continues to validate the physical
   half-spectrum as `range_fft_size // 2 + 1` bins.
3. Correlation and idle calculations now promote measured IQ to `complex128`
   and powers to `float64`, suppress expected arithmetic warnings locally, and
   explicitly reject non-finite correlation, energy, denominator, ratio, or dB
   metrics with `ChirpSyncError`.

The initial covering run exercised the unfixed behavior and reported 17 tests
with 8 failures and 2 errors: missing chirp metadata, accepted invalid FFT
sizes, and an overflowed finite-input synchronization case that did not raise.

Final covering verification:

```text
python -m unittest tests.test_radar_config tests.test_radar_processor -v
Ran 17 tests in 0.305s
OK
```

Final full verification:

```text
python -m unittest discover -s tests -p "test_*.py" -v
Ran 26 tests in 0.276s
OK
```

The final runs produced no test failures, errors, or overflow warnings. No Task
3 concerns remain from these review findings.
