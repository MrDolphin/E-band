# QPSK Earliest Preamble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the streaming QPSK decoder from skipping earlier complete packets when a later buffered packet has a slightly higher preamble correlation.

**Architecture:** Keep the existing global-best fallback for weak or incomplete captures, but prefer the earliest preamble whose full correlation reaches a high-confidence lock threshold. The change remains inside `QpskModem`; `QpskStreamDecoder` and the wire protocol stay unchanged.

**Tech Stack:** C# 12, .NET 8, existing console self-test.

---

### Task 1: Reproduce Packet Skipping

**Files:**
- Modify: `Material/videotool_decompiled/remotevideo_usepdb/video_modem_selftest/Program.cs`

- [x] **Step 1: Write the failing test**

Create two back-to-back valid QPSK packets. Add deterministic noise only to the first packet so its correlation remains reliable but lower than the second packet. Append both to `QpskStreamDecoder` and assert that the first decoded bytes equal the first packet.

- [x] **Step 2: Run test to verify it fails**

Run the self-test and expect a failure stating that the streaming decoder skipped the earlier packet.

### Task 2: Select the Earliest Reliable Preamble

**Files:**
- Modify: `Material/videotool_decompiled/remotevideo_usepdb/remotevideo/QpskModem.cs`

- [x] **Step 1: Implement the minimal selection change**

During the coarse preamble scan, retain the existing best candidate but stop scanning once the earliest fully evaluated candidate reaches a fixed high-confidence correlation. Refine timing around that candidate as today. If no high-confidence candidate exists, retain the existing global-best behavior.

- [x] **Step 2: Run all self-tests**

Expected: the new back-to-back regression and all existing modem/FMCW tests pass.

- [x] **Step 3: Build Release**

Expected: zero build errors.

- [ ] **Step 4: Commit and publish**

Commit only the plan, modem source, and self-test. Push `stage-14`, publish a commit-ID-named build, and update `run_remotevideo.cmd`.
