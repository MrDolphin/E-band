# AGENTS.md

This workspace is for ANTSDR E310 / remotevideo development. Keep context small. Avoid scanning large binary/material folders unless the user explicitly asks.

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
2. Use `rg --files` or `rg -n` to locate exact files.
3. Read only relevant sections around matches.
4. Summarize long logs instead of dumping them.
5. Run the smallest useful verification command.
6. Final responses should report changed files, verification, and remaining hardware/manual checks.
