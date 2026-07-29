# Remotevideo workflow

Read this file only for ANTSDR E310 remotevideo work. FMCW radar tasks should
use the root `AGENTS.md` references instead.

## Starting points

1. `ANTSDR_E310_REMOTEVIDEO_HANDOFF.md`
2. `Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj`
3. The exact source and test files implicated by current evidence.

Common source locations:

- PC app: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/MainWindow.cs`
- Frame reassembly: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/WirelessVideoFrame.cs`
- QPSK modem: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskModem.cs`
- Stream decoder: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskStreamDecoder.cs`
- Signal analysis: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/RxSignalAnalyzer.cs`
- Self-test: `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest`
- E310 bridge: `Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/video_modem_bridge.c`
- Build script: `Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/build_video_modem_bridge.sh`

## Avoid by default

Do not recursively read `Ubuntu2204.appx`, `target-sysroot/`, `tools/`,
`picture/`, `Material/ANTSDR_E200_R1.0/`, `bin/`, `obj/`, generated binaries,
stage output, or copied/decompiled duplicate trees.

Use targeted search:

```powershell
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/remotevideo
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
```

## Build and test

```powershell
dotnet build Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj
dotnet run --project Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/video_modem_selftest.csproj
```

Build the E310 bridge from WSL/Ubuntu:

```bash
cd /mnt/d/hp-laptop/E-band-fmcw/Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
./build_video_modem_bridge.sh
```

E310 normally has no native `gcc`; use the configured ARM cross-compiler and
`target-sysroot`.

## Working assumptions

- E310 IP: `192.168.1.10`
- PC IP: `192.168.1.200`
- E310 UDP listen port: `8080`
- PC RX IQ port: `8098`
- Link: PC app → E310 TX1 → external RF/link → E310 RX1 → UDP IQ to PC
- QPSK defaults: LO about `900 MHz`, sample rate `3.84 MSPS`, symbol rate
  `960 ksym/s`, four samples per symbol.

Confirm these assumptions before hardware-facing changes.
