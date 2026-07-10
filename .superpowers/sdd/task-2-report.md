# Task 2 Report: Move Mode 9 source frame production onto fixed-cadence scheduling

## Status

Implemented and verified on branch `codex-mode9-realtime-refactor`.

## Requirements handled

- Added a focused regression test for bounded real-time repair scheduling before production changes.
- Implemented bounded scheduler repair slicing so one cycle never exceeds `VideoRealtimePolicy.GetRepairChunkBudget(newFrameChunkCount)`.
- Wired Mode 9 real-time sending through `VideoSendScheduler` for first-pass frame fragmentation/admission and bounded older-frame repair work.
- Preserved still-photo and non-real-time repair behavior.

## TDD evidence

### Red

1. Added `TestRealtimeSchedulerLimitsRepairBudget()` to `video_modem_selftest/Program.cs`.
2. First exact selftest run was blocked by the known locked/dirty `obj` path problems.
3. After fixing the selftest build harness, reran:

```powershell
dotnet run --project D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\video_modem_selftest\video_modem_selftest.csproj
```

Observed failure:

- `System.InvalidOperationException: repair backlog must preserve remaining chunks across later cycles`

This was the intended logic failure against the Task 1 scheduler stub.

### Green

After implementing the scheduler and MainWindow changes, reran:

```powershell
dotnet run --project D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\video_modem_selftest\video_modem_selftest.csproj
dotnet build D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo.csproj
```

Results:

- Selftest passed: `All video modem self-tests passed.`
- WPF build passed: `0 Error(s)` with existing warnings only.

## Implementation summary

### `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/VideoSendScheduler.cs`

- Replaced the Task 1 stub with a real bounded repair queue.
- Added internal pending-repair state so a large frame can be drained across multiple cycles.
- Added one-cycle eligibility delay (`firstSeenMs + 1`) so the current frame is not immediately re-repaired in the same send cycle.
- Preserved expiry by removing expired frames from both the dictionary and the queue.

### `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/MainWindow.cs`

- Added `m_videoSendScheduler` state and reset it when a new send session starts.
- Changed the Mode 9 `realtime` branch in `SendVideoModemHardwareFrame(...)` to:
  - enqueue/frame-fragment via `VideoSendScheduler`
  - send the new frame first-pass immediately
  - drain only bounded repair work after that
- Left the non-real-time path and existing repair loops in place.
- Added `SendVideoWorkItem(...)` to reuse the existing wireless chunk send path without duplicating chunk-loop logic.

### `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/Program.cs`

- Added `TestRealtimeSchedulerLimitsRepairBudget()`.
- Verified:
  - repair work appears for a fresh queued frame
  - each cycle stays within budget
  - each returned work item stays within budget
  - remaining chunks survive across later cycles until the queued repair work is exhausted

## Build/test harness fixes required in this workspace

These were necessary to make the exact required commands runnable in this environment:

### `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/video_modem_selftest.csproj`

- Added `EnableDefaultCompileItems=False`
- Added explicit `Compile Include="Program.cs"`
- Added `EnableSourceLink=False`

### `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/Directory.Build.props`

- Redirected selftest `bin/obj` outputs to `codex_tmp_build25/video_modem_selftest/...` so the exact `dotnet run --project ...` command avoids the locked local `obj` tree.

### `Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj`

- Added `GenerateTargetFrameworkAttribute=False`
- Added `Compile Remove="obj\**\*.cs"`

### `Material/videotool_decompiled/remotevideo_usepdb/Directory.Build.props`

- Redirected `remotevideo` project `bin/obj` outputs to `codex_tmp_build25/remotevideo/...` so the exact `dotnet build ...` command avoids the locked local `obj` tree.

## Files changed

- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo\MainWindow.cs`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo\VideoSendScheduler.cs`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\remotevideo.csproj`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\Directory.Build.props`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\video_modem_selftest\Program.cs`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\video_modem_selftest\video_modem_selftest.csproj`
- `D:\hp-laptop\E-band\Material\videotool_decompiled\remotevideo_usepdb\video_modem_selftest\Directory.Build.props`

## Self-review

- The new test exercises the actual Task 2 behavior that was missing from Task 1.
- Scheduler repair work is bounded, resumable, and expires cleanly.
- Real-time Mode 9 no longer does synchronous multi-round repair loops inside the per-frame send path.
- Non-real-time/still-image behavior stays on the old path.
- The build-path fixes are narrow to the affected projects and were required by this workspace's locked generated directories.

## Concerns

- During real-time playback, in-cycle repair traffic now comes from the bounded scheduler queue rather than `RepairRecentVideoFrames(...)`'s receiver-missing-aware selection. The existing missing-aware repair helpers are still used for non-real-time sends and final backlog draining, but live hardware verification should confirm the bounded source-side repair cadence performs as expected on the E310 link.
