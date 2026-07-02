# Versioned Build Identity

## Goal

Every published PC application must carry one immutable seven-character Git commit ID in three places:

- Window title: `视频流测试工具 v1.1 (b554211)`
- Directory: `video-modem-stage14-<label>-b554211`
- Executable: `remotevideo-b554211.exe`

## Design

`publish_versioned_remotevideo.ps1` reads the current commit once and passes it to MSBuild as `BuildCommit`. MSBuild embeds that value as assembly metadata and uses it in `AssemblyName`. `AppBuildInfo` reads only embedded metadata, never the runtime repository HEAD. The script creates the matching output directory and updates `run_remotevideo.cmd` only when `-UpdateLauncher` is explicitly supplied after a successful build.

## Verification

- A self-test verifies embedded commit selection and seven-character normalization.
- Release build output must contain matching EXE/DLL/runtime files.
- Reflection over the output DLL must report the same `BuildCommit` value.
- Running or copying the build outside the repository must not change its title commit.
