# Latest Frame Repair Priority

## Evidence

The `20260702_012332` run recovered 40 frames, but 21 completed after a newer frame was already displayed. Repair traffic was roughly half of all transmitted chunks. The current scheduler orders candidates by missing count before frame ID, so nearly complete old frames consume repair bandwidth even when they can no longer advance the picture.

## Design

- Read a thread-safe snapshot of the latest displayed frame ID.
- Do not repair frames at or before that displayed frame.
- Sort eligible repair candidates by newest frame first, then by missing count.
- Keep modulation, thresholds, chunk size, repair budget, and E310 unchanged.

## Verification

- Add failing self-tests for stale-frame exclusion and newest-first priority.
- Implement only the repair eligibility and ordering change.
- Run the full self-test and Release build.
- Publish a commit-ID build and update `run_remotevideo.cmd`.
