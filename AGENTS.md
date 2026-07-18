# AGENTS.md

This workspace is for ANTSDR E310 / remotevideo development. Keep context small. Avoid scanning large binary/material folders unless the user explicitly asks.

## FMCW Radar Workflow

For FMCW radar work, this section takes precedence over the legacy remotevideo
startup list below. Read only the smallest relevant source set first:

1. `docs/superpowers/plans/2026-07-14-fmcw-synthetic-radar-mvp.md`
2. `docs/superpowers/specs/2026-07-14-e310-eband-fmcw-radar-design.md`
3. `docs/superpowers/2026-07-15-fmcw-radar-mvp-overall-report.zh-CN.md`
4. The exact radar source and test files implicated by the current evidence.

### Hardware gate

- Default to **hardware disconnected**. Do not access E310, transmit, capture,
  or change device state unless the user confirms in the current turn that the
  physical chain is connected and authorizes the test.
- When hardware is unavailable, work only on synthetic/replay tests, offline
  performance, diagnostics, documentation, and reviewable code defects.
- Do not make speculative radar-algorithm changes without a failing test,
  profiler result, replay capture, or a concrete hardware observation.

### Lean development loop

1. Run `git status --short`; preserve all untracked captures, artifacts,
   caches, and board-control material.
2. For code discovery, use codebase-memory project
   `D-hp-laptop-E-band-fmcw` first (`search_graph`, `trace_path`,
   `get_code_snippet`). Use targeted `rg` only for literals, non-code files,
   or an insufficient graph result.
3. State the smallest falsifiable hypothesis, then add or run the smallest
   focused test. Run the full suite only before a phase commit or after a
   cross-module change.
4. Commit and push one coherent, verified phase. Add one PR comment per phase
   containing evidence, not a comment for every small exploratory action.
5. If there is no safe offline action with evidence behind it, stop without
   code churn and record the required hardware observation for the next phase.

### Token-efficient reporting

- Keep raw IQ, screenshots, long logs, and generated outputs in untracked
  `artifacts/`; report only configuration, measured diagnostics, result, and
  artifact path.
- Do not paste large source files or repeat prior test logs. Refer to the
  overall report and PR comments for durable history.

## Start Here

Read only the smallest useful set first:

1. `ANTSDR_E310_REMOTEVIDEO_HANDOFF.md`
2. `Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj`
3. The exact source files related to the user's request

Common source files:

- PC app: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/MainWindow.cs`
- Frame split/reassembly: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/WirelessVideoFrame.cs`
- QPSK modem: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskModem.cs`
- Stream decoder: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskStreamDecoder.cs`
- Signal analysis: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/RxSignalAnalyzer.cs`
- Self-test: `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest`
- E310 bridge: `Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/video_modem_bridge.c`
- E310 build script: `Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/build_video_modem_bridge.sh`

## Avoid by Default

Do not recursively read or summarize:

- `Ubuntu2204.appx`
- `target-sysroot/`
- `tools/`
- `picture/`
- `Material/ANTSDR_E200_R1.0/`
- `**/bin/`
- `**/obj/`
- generated executables and stage output folders
- copied/decompiled duplicate trees unless comparing copies is requested

Use targeted search:

```powershell
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/remotevideo
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
```

For broad search, exclude heavy paths:

```powershell
rg -n "keyword" -g "!Ubuntu2204.appx" -g "!target-sysroot/**" -g "!tools/**" -g "!**/bin/**" -g "!**/obj/**"
```

## Build and Test

PC app:

```powershell
dotnet build Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj
```

Self-test:

```powershell
dotnet run --project Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/video_modem_selftest.csproj
```

E310 bridge from WSL/Ubuntu:

```bash
cd /mnt/d/hp-laptop/E-band/Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
./build_video_modem_bridge.sh
```

The E310 firmware normally does not have native `gcc`; build ARM binaries with the configured cross compiler and `target-sysroot`.

## Current Facts

- E310 IP: `192.168.1.10`
- PC IP: `192.168.1.200`
- E310 UDP listen port: `8080`
- PC RX IQ port: `8098`
- Link focus: PC `remotevideo` -> E310 TX1 -> external RF/link -> E310 RX1 -> UDP IQ back to PC
- QPSK defaults: LO around `900 MHz`, sample rate `3.84 MSPS`, symbol rate `960 ksym/s`, `4 samples/symbol`

Treat hardware/network values as working assumptions. Confirm before risky hardware changes.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `MrDolphin/E-band`; external PRs are not treated as a triage request surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock skills triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context domain docs layout: root `CONTEXT.md` plus `docs/adr/`. See `docs/agents/domain.md`.

## Editing Rules

- Make narrow edits in the file that owns the behavior.
- Preserve the existing WPF/decompiled-code style.
- Do not delete generated binaries or copied folders unless explicitly asked.
- Some Chinese Markdown may display with mojibake in PowerShell; do not rewrite large documents only to fix display encoding.
- For hardware-facing changes, report required device-side verification.

## Git Checkpoints

- If this workspace is a Git repository, create a commit automatically after each completed code change when build/self-test verification passes.
- After a successful local commit, push to `origin` automatically when a remote is configured and reachable.
- Before editing, run `git status --short` and preserve unrelated user changes.
- Do not commit generated build output, copied stage folders, `bin/`, `obj/`, large archives, or hardware sysroot/toolchain folders.
- Use concise commit messages that name the feature or fix, for example `Improve FMCW sweep-lock diagnostics`.
- If verification fails or cannot be run, do not commit; report the changed files and the failed/missing verification.
- Tag hardware-known-good versions only when the user explicitly says the version works on E310 hardware.

## Token-Saving Workflow

1. Identify the subsystem: PC UI/app, modem/framing, self-test, or E310 bridge.
2. For code definitions and call paths, use codebase-memory first; use `rg` for
   literal/config/document searches and as a targeted fallback.
3. Read only relevant sections around matches.
4. Summarize long logs instead of dumping them.
5. Run the smallest useful verification command.
6. Final responses should report changed files, verification, and remaining hardware/manual checks.
