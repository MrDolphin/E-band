# AGENTS.zh.md

这是 `AGENTS.md` 的中文说明版，给人阅读用。为了减少 Codex 每次启动/读取项目规则时的 token 支出，默认执行规则放在精简英文版 `AGENTS.md` 中。

## 核心目标

这个工作区主要用于 ANTSDR E310 和 `remotevideo` 项目开发。后续让 Codex 工作时，应该尽量少读无关文件，优先定位上位机、QPSK/视频链路、自测工程或 E310 bridge/receiver 的相关源码。

## 默认先读

优先读取最小上下文：

1. `ANTSDR_E310_REMOTEVIDEO_HANDOFF.md`：当前状态、关键路径、端口、链路假设、阶段记录。
2. `Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj`：WPF 工程目标和依赖。
3. 和当前任务直接相关的源码文件。

常用源码位置：

- PC 上位机主逻辑：`Material/videotool_decompiled/remotevideo_usepdb/remotevideo/MainWindow.cs`
- 视频帧分片/重组：`Material/videotool_decompiled/remotevideo_usepdb/remotevideo/WirelessVideoFrame.cs`
- QPSK 调制解调：`Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskModem.cs`
- QPSK 流式解码：`Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskStreamDecoder.cs`
- 信号分析：`Material/videotool_decompiled/remotevideo_usepdb/remotevideo/RxSignalAnalyzer.cs`
- 自测工程：`Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest`
- E310 bridge：`Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/video_modem_bridge.c`
- E310 构建脚本：`Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver/build_video_modem_bridge.sh`

## 默认跳过

这些目录或文件很大，或者主要是构建产物/资料包，除非任务明确需要，不要递归读取：

- `Ubuntu2204.appx`
- `target-sysroot/`
- `tools/`
- `picture/`
- `Material/ANTSDR_E200_R1.0/`
- `**/bin/`
- `**/obj/`
- 各类生成的 `.exe`、`udp_receiver`、`video_modem_bridge`、stage 输出目录
- 反编译副本目录，除非用户要求比较副本

## 搜索方式

优先小范围搜索：

```powershell
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/remotevideo
rg -n "keyword" Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
```

必须全局搜索时，排除大目录：

```powershell
rg -n "keyword" -g "!Ubuntu2204.appx" -g "!target-sysroot/**" -g "!tools/**" -g "!**/bin/**" -g "!**/obj/**"
```

## 构建和测试

PC 上位机：

```powershell
dotnet build Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj
```

自测工程：

```powershell
dotnet run --project Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/video_modem_selftest.csproj
```

E310 bridge 在 WSL/Ubuntu 中交叉编译：

```bash
cd /mnt/d/hp-laptop/E-band/Material/videotool_decompiled/remotevideo_usepdb/e310_udp_receiver
./build_video_modem_bridge.sh
```

E310 固件通常没有板载 `gcc`，不要假设可以直接在板子上编译；通常需要交叉编译并配合 `target-sysroot`。

## 当前常用参数

- E310 IP：`192.168.1.10`
- PC IP：`192.168.1.200`
- E310 UDP 监听端口：`8080`
- PC RX IQ 端口：`8098`
- 当前链路重点：PC `remotevideo` -> E310 TX1 -> 外部射频/链路 -> E310 RX1 -> UDP IQ 回传 PC
- QPSK 默认参数：LO 约 `900 MHz`，采样率 `3.84 MSPS`，符号率 `960 ksym/s`，`4 samples/symbol`

这些是当前工作假设，不是永远不变的常量。涉及硬件发射、增益、频率、连线方式时，需要再次确认。

## 修改原则

- 只改和任务直接相关的文件。
- 尽量保持现有 WPF/反编译代码风格。
- 不要主动删除生成二进制、构建产物或副本目录。
- 现有中文 Markdown 在 PowerShell 中可能显示乱码；不要仅因为终端显示乱码就大规模重写文档。
- 涉及硬件侧行为时，最终说明还需要哪些板端/链路验证。

## 省 token 工作流

1. 先判断任务属于 PC UI/app、modem/framing、自测工程还是 E310 bridge。
2. 用 `rg --files` 或 `rg -n` 精准定位文件。
3. 只读取匹配行附近的相关片段。
4. 长日志只总结关键行，不整段贴出。
5. 执行最小必要验证命令。
6. 最终回复只报告改了什么、跑了什么验证、还剩哪些硬件/人工确认。
