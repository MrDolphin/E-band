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
