# Mode 9 Real-Time Video Loopback Design

Date: 2026-07-09

## Context

This project uses an ANTSDR E310 as a QPSK video loopback path:

- PC `remotevideo` starts transmission
- PC encodes source video/image frames into QPSK IQ
- E310 receives UDP IQ, transmits on TX1, receives on RX1 through loopback or wireless path
- E310 returns RX IQ to the PC over UDP
- PC demodulates, reassembles, and displays received video

The current Mode 9 path can sometimes recover frames, but the user-observed behavior is still poor:

- long black-screen periods before the first visible RX frame
- recovered frames arriving late and displaying out of temporal order
- visible backward jumps or stutter due to late old frames replacing newer ones
- excessive coupling between new-frame transmission and old-frame repair
- diagnostics that mix source recovery and actual display continuity, making regressions hard to interpret

The design target is not minimum theoretical latency. The target is a practical real-time loopback pipeline that favors stable playback and frame continuity first, then frame completeness, then lower latency.

## Goals

Priority order:

1. Keep RX playback visually continuous and stable
2. Maximize recoverable and displayable complete frames
3. Keep end-to-end delay within an acceptable range, with 1-2 seconds acceptable if playback is stable

Success means:

- RX display should avoid long black-screen gaps once playback has started
- displayed frames must move forward monotonically in time
- late recovered old frames must not pull playback backward
- repair traffic must stay bounded so old-frame repair cannot starve new-frame transmission
- telemetry must distinguish "recovered", "displayable", and "actually displayed"

## Non-Goals

This design does not target:

- zero RF or UDP loss
- perfect recovery of every source frame
- minimum possible latency at any cost
- maximizing repair volume
- using parameter tuning as the primary optimization path

The system is allowed to discard late or stale frames if doing so improves playback continuity.

## High-Level Strategy

The correct optimization direction is to treat Mode 9 as a bounded real-time playback pipeline, not as a "repair every old frame until complete" transport.

Core policy:

- new-frame first transmission has priority over repair
- repair is limited by explicit bandwidth and time budgets
- full recovery does not automatically imply display eligibility
- display order must be monotonic
- stale frames are intentionally dropped
- diagnostics must separately track transport success and playback success

## Module Boundaries

The existing flow concentrates too much behavior in `MainWindow.cs`, especially startup, decoder reset, demodulation, frame parsing, reassembly, display progression, and diagnostics. The Mode 9 design should be expressed as four deep modules with smaller interfaces and stronger locality.

### 1. Send Scheduler Module

Purpose:

- admit newly produced source frames
- fragment each compressed frame
- decide whether the next transmit opportunity is for first-send or repair
- enforce repair budget, frame expiry, and freshness priority

Interface responsibilities:

- input: newly encoded source frame plus current link feedback
- output: next batch of chunks to modulate and transmit

This module should not know about WPF controls, image display, or IQ demodulation internals.

### 2. Receive Reassembly Module

Purpose:

- accept decoded video chunks
- track per-frame chunk membership
- detect complete frames
- detect nearly complete but expired frames
- discard stale or timed-out frame assemblies

This module should not decide what is shown on screen.

### 3. Display Sequencer Module

Purpose:

- decide which recovered frame is actually eligible for display
- guarantee monotonic frame progression
- keep the previous frame visible when no better frame is ready
- reject late complete frames that would cause backward playback jumps

This module is the main fix for the current "recovered but still visually wrong" behavior.

### 4. Diagnostics and Metrics Module

Purpose:

- record sender, transport, recovery, and display metrics separately
- expose clear evidence for whether failure is at source admission, chunk delivery, frame recovery, or display sequencing

This module should observe state, not drive transport decisions.

## Data Flow

The intended steady-state pipeline is:

`Source frame -> compression budget -> fragmentation -> send scheduling -> QPSK modulation -> E310 TX/RX loopback -> RX IQ return -> QPSK demodulation -> chunk parse -> frame reassembly -> display sequencing -> screen`

Important separation points:

- "frame complete" is a recovery-layer fact
- "frame eligible for display" is a playback-layer fact
- diagnostics observe both facts independently

This separation is required to prevent old recovered frames from corrupting playback order.

## Sending Policy

The sender must follow a bounded real-time schedule.

### Source Frame Production

New source frames should be produced at a stable cadence rather than waiting for older frames to be repaired first.

Compression output for each source frame must stay within a bounded size budget:

- bounded long-edge size
- bounded WebP byte size
- bounded chunk count

Frame size variance should be limited. A single oversized frame must not suddenly consume multiple frame periods of channel time.

### First-Send vs Repair

Transmission opportunities are split into:

- first-send traffic for new frames
- repair traffic for previously incomplete frames

Policy:

- first-send traffic is the primary channel
- repair uses only a limited fraction of total sending budget
- repair must never block source freshness

If an old frame remains incomplete after its repair budget or freshness window is exhausted, it should be abandoned.

### Expiry

A source frame can be abandoned at the sender when:

- it has fallen behind the newest source frame by more than the configured freshness window
- its repair budget is exhausted
- the sender queue is congested and preserving the newest frame is more valuable than preserving the stale one

This keeps the system aligned with playback continuity rather than archival completeness.

## Receive Reassembly Policy

The reassembly layer tracks each frame as an object with a lifecycle:

- collecting
- nearly complete
- complete
- expired
- stale

### Reassembly Rules

- add incoming chunks to the owning frame assembly
- mark a frame complete only when all required chunks are present
- keep explicit accounting for missing chunks
- mark incomplete assemblies expired when their time window closes
- evict stale assemblies to keep memory and complexity bounded

The reassembly layer should make "almost complete but too late" visible in metrics rather than keeping those frames alive indefinitely.

## Display Sequencing Policy

Playback continuity depends more on display sequencing than on raw frame recovery percentage.

### Monotonic Display

Maintain `LastDisplayedFrameId`.

Rules:

- if `frameId <= LastDisplayedFrameId`, do not display the frame
- if a frame is complete but already too old relative to current display position, drop it
- if no newer complete frame is available, keep showing the last displayed frame instead of black-screening

### Black-Screen Policy

Black-screen should only be expected before the first displayable frame appears.

After the first successful display:

- no new complete frame -> keep previous frame
- new complete frame with higher `frameId` -> display it
- old complete frame arriving late -> reject it

This directly addresses the current "long black period, then a few late frames appear" and "visual backward jumps" symptoms.

### Display vs Recovery

A frame can be:

- recovered but not displayed because it is late
- displayed even when other older complete frames are still being finalized in the background

This is intentional and should be visible in metrics.

## Metrics and Telemetry

To avoid human interpretation errors, metrics should be separated by layer.

### Sender Metrics

- source frames produced
- current source frame production rate
- average compressed frame size
- average chunks per source frame
- first-send chunk count
- repair chunk count
- repair bandwidth share

### Reassembly Metrics

- cumulative RX chunks
- complete recovered frame count
- near-complete unfinished frame count
- recovery rate
- average recovery latency
- timed-out frame count

### Display Metrics

- displayed frame count
- out-of-order dropped frame count
- late dropped frame count
- complete-but-not-displayed frame count
- first-frame display latency
- recent display frame rate

### Physical/Decode Health Metrics

- preamble correlation
- I/Q power imbalance
- coarse frequency offset
- CRC fail count
- format fail count
- air-loss or transport loss indicators

### Key Metrics To Watch First

For short operator diagnosis, prioritize:

1. source frame production rate
2. repair bandwidth share
3. frame recovery rate
4. average recovery latency
5. actual display frame rate
6. late/out-of-order drop counts

These answer whether the problem is slow source generation, over-repair, slow recovery, or display policy.

## Acceptance Criteria

The design is considered successful when:

- RX playback no longer shows frequent backward time jumps
- black-screen duration after startup is materially reduced
- once playback starts, the system mostly holds the previous frame rather than black-screening
- display uses newer frames preferentially rather than waiting indefinitely on stale repairs
- repair traffic stays bounded and does not collapse source frame cadence
- operators can distinguish "transport success" from "playback success" from built-in metrics

The design does not require every source frame to be recovered, only that playback becomes predictably smooth and ordered within the allowed latency envelope.

## Implementation Order

Recommended order:

### Step 1: Separate display sequencing from frame recovery

Implement monotonic display progression, stale-frame rejection, and hold-last-frame policy first.

Reason:

- this most directly improves visual behavior
- it resolves backward jumps and unnecessary black-screening without needing transport redesign first

### Step 2: Rework send scheduling

Implement:

- first-send priority
- bounded repair budget
- frame expiry at the sender
- stable source frame admission cadence

Reason:

- this addresses the main cause of "slow send, old frame drag, new frame starvation"

### Step 3: Harden reassembly lifecycle management

Implement explicit timeouts, stale-assembly eviction, and near-complete tracking.

Reason:

- this prevents assemblies from living too long and gives cleaner visibility into "just short of complete" failures

### Step 4: Perform lower-level parameter and physical-layer tuning

Only after system behavior is structurally stable should additional tuning focus on:

- preamble thresholds
- fast filter thresholds
- search step
- IQ compensation
- finer decode robustness

Reason:

- current failures are primarily systemic control-flow and scheduling issues
- low-level tuning should not remain the first resort

## Tradeoff Summary

This design intentionally trades away:

- perfect source-frame completeness
- unbounded repair persistence
- theoretical minimum latency

In exchange for:

- smoother visual playback
- correct temporal ordering
- bounded backlog growth
- metrics that clearly identify the active bottleneck

## Final Recommendation

Proceed with a Mode 9 redesign centered on bounded real-time playback rather than best-effort total frame repair.

The strongest architectural principle for this codebase is:

**new frames must keep moving, old frames may be abandoned, and display must never move backward.**
