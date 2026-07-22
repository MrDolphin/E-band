# AGENTS.md

This workspace contains ANTSDR E310 FMCW radar and remotevideo development.
Keep context small and make evidence-driven, narrowly scoped changes.

## Staged execution gate

Large work must be split into stages. Complete only one stage per user approval:

1. **Goal 1 — 调研报告：** only investigate and report; do not design or edit.
2. **Goal 2 — 方案规划：** only produce a bounded plan; do not implement.
3. **Goal 3 — 单一功能：** implement one explicitly approved feature only.
4. **Goal 4 — 测试修复：** test that feature and fix only exposed defects.

At the end of a stage:

- Report `Goal N：已完成` with concise evidence.
- Stop and wait for explicit confirmation before opening the next goal.
- Earlier requests to “continue”, “finish everything”, or “keep going” do not
  override this gate.
- Verification, one coherent commit/push, and one evidence-based PR comment
  belonging to the approved stage are allowed before stopping.

## Scope selection

- Identify the subsystem first: FMCW radar, PC remotevideo UI, modem/framing,
  self-test, or E310 bridge.
- Read only the smallest relevant source and test set.
- FMCW starting references:
  1. `docs/superpowers/plans/2026-07-14-fmcw-synthetic-radar-mvp.md`
  2. `docs/superpowers/specs/2026-07-14-e310-eband-fmcw-radar-design.md`
  3. `docs/superpowers/2026-07-15-fmcw-radar-mvp-overall-report.zh-CN.md`
- For remotevideo work, read `docs/agents/remotevideo-workflow.md` only when
  that subsystem is in scope.
- Do not recursively scan large binary/material folders unless explicitly
  requested.

## Hardware gate

- Default to **hardware disconnected**.
- Do not access E310, transmit, capture, or change device state unless the user
  confirms in the current turn that the physical chain is connected and
  authorizes the test.
- Without hardware, limit work to synthetic/replay tests, offline diagnostics,
  documentation, and evidence-backed code defects.
- Do not make speculative radar-algorithm changes without a failing test,
  profiler/replay result, or concrete hardware observation.
- Hardware-facing changes must state the remaining device-side verification.

## Lean development loop

1. Run `git status --short`; preserve unrelated changes and all untracked
   captures, `artifacts/`, caches, board-control material, and generated files.
2. For code discovery, prefer codebase-memory project
   `D-hp-laptop-E-band-fmcw`: `search_graph`, `trace_path`, then
   `get_code_snippet`. Use targeted `rg` for literals, non-code files, or when
   graph results are insufficient.
3. State the smallest falsifiable hypothesis. Add or run the smallest focused
   test; run the full suite only before a phase commit or after cross-module
   changes.
4. Make narrow edits in the file that owns the behavior. Preserve existing
   WPF/decompiled-code style and do not rewrite large Chinese documents merely
   because PowerShell displays mojibake.
5. If no safe, evidence-backed offline action remains, stop without code churn
   and record the required hardware observation.

## Git checkpoints

- Commit automatically after a coherent code phase only when relevant
  verification passes; then push when `origin` is configured and reachable.
- Do not commit captures, `artifacts/`, caches, `bin/`, `obj/`, generated
  binaries, archives, copied stage folders, or toolchain/sysroot folders.
- Keep unrelated user changes out of the phase commit.
- If verification fails or cannot run, do not commit; report changed files and
  the failed or missing verification.
- Add at most one PR comment per phase and include evidence rather than raw
  exploratory logs.
- Tag a hardware-known-good version only when the user explicitly confirms it
  works on E310 hardware.

## Token-efficient reporting

- Keep raw IQ, screenshots, long logs, and generated outputs in untracked
  `artifacts/` and report only configuration, measured result, and artifact
  path.
- Do not paste large source files or repeat prior logs; link durable reports or
  PR comments.
- Final responses should contain only: outcome, changed files, verification,
  remaining hardware/manual checks, and current Goal status.

## Project references

- Issue workflow: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation workflow: `docs/agents/domain.md`
