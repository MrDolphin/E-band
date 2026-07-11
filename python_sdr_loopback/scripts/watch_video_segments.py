from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zlib
from pathlib import Path


SEGMENT_RE = re.compile(r"segment_(\d+)\.bin$")


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def load_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def segment_index(path: Path) -> int | None:
    match = SEGMENT_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1))


def stable_file(path: Path, settle_sec: float) -> bool:
    try:
        first_size = path.stat().st_size
    except FileNotFoundError:
        return False
    time.sleep(settle_sec)
    try:
        second_size = path.stat().st_size
    except FileNotFoundError:
        return False
    return first_size == second_size


def manifest_by_index(manifest: dict | None) -> dict[int, dict]:
    if not manifest:
        return {}
    by_index: dict[int, dict] = {}
    for segment in manifest.get("segments", []):
        try:
            by_index[int(segment.get("index", 0))] = dict(segment)
        except (TypeError, ValueError):
            continue
    return by_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch recovered SDR video segments and build a growing output file.")
    parser.add_argument("--segment-dir", type=Path, default=Path("artifacts/video_segments"))
    parser.add_argument("--manifest-file", type=Path, help="Defaults to <segment-dir>/manifest.json.")
    parser.add_argument("--output-file", type=Path, default=Path("artifacts/video_segments_live.mp4"))
    parser.add_argument("--poll-sec", type=float, default=0.25)
    parser.add_argument("--file-settle-sec", type=float, default=0.05)
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=0.0,
        help="Stop if no complete manifest appears before this many seconds. 0 means no timeout.",
    )
    args = parser.parse_args()

    if args.poll_sec <= 0.0:
        print("--poll-sec must be positive", file=sys.stderr)
        return 2
    if args.file_settle_sec < 0.0:
        print("--file-settle-sec must be non-negative", file=sys.stderr)
        return 2
    if args.timeout_sec < 0.0:
        print("--timeout-sec must be non-negative", file=sys.stderr)
        return 2

    manifest_file = args.manifest_file or args.segment_dir / "manifest.json"
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"watch_segment_dir={args.segment_dir}")
    print(f"watch_manifest_file={manifest_file}")
    print(f"watch_output_file={args.output_file}")

    processed: set[int] = set()
    output_crc = 0
    output_bytes = 0
    next_index = 1
    started_at = time.perf_counter()
    last_progress_at = started_at

    with args.output_file.open("wb") as output:
        while True:
            manifest = load_manifest(manifest_file)
            expected_total = int(manifest.get("file_batches_total", 0)) if manifest else 0
            manifest_segments = manifest_by_index(manifest)

            while True:
                candidate = args.segment_dir / f"segment_{next_index:04d}.bin"
                if not candidate.exists() or not stable_file(candidate, float(args.file_settle_sec)):
                    break

                data = candidate.read_bytes()
                output.write(data)
                output.flush()
                output_crc = zlib.crc32(data, output_crc) & 0xFFFFFFFF
                output_bytes += len(data)
                processed.add(next_index)
                last_progress_at = time.perf_counter()

                expected = manifest_segments.get(next_index)
                crc_ok = True
                size_ok = True
                offset_ok = True
                if expected:
                    crc_ok = crc32(data) == int(expected.get("crc32", -1))
                    size_ok = len(data) == int(expected.get("size", -1))
                    offset_ok = output_bytes - len(data) == int(expected.get("offset", -1))

                print(f"watch_segment={next_index}")
                print(f"watch_segment_file={candidate}")
                print(f"watch_segment_bytes={len(data)}")
                print(f"watch_output_bytes={output_bytes}")
                print(f"watch_crc_ok={str(crc_ok).lower()}")
                print(f"watch_size_ok={str(size_ok).lower()}")
                print(f"watch_offset_ok={str(offset_ok).lower()}")
                next_index += 1

            if manifest:
                expected_output_bytes = int(manifest.get("output_bytes", 0))
                expected_output_crc = int(manifest.get("output_crc32", 0))
                complete = expected_total > 0 and len(processed) >= expected_total
                if complete:
                    output_size_ok = output_bytes == expected_output_bytes
                    output_crc_ok = output_crc == expected_output_crc
                    watch_ok = bool(manifest.get("file_ok")) and output_size_ok and output_crc_ok
                    print(f"watch_segments_processed={len(processed)}")
                    print(f"watch_expected_segments={expected_total}")
                    print(f"watch_output_crc32={output_crc}")
                    print(f"watch_expected_output_crc32={expected_output_crc}")
                    print(f"watch_output_size_ok={str(output_size_ok).lower()}")
                    print(f"watch_output_crc_ok={str(output_crc_ok).lower()}")
                    print(f"watch_elapsed_sec={time.perf_counter() - started_at:.3f}")
                    print(f"watch_ok={str(watch_ok).lower()}")
                    return 0 if watch_ok else 1

            if args.timeout_sec and time.perf_counter() - started_at >= args.timeout_sec:
                print(f"watch_timeout=true")
                print(f"watch_segments_processed={len(processed)}")
                print(f"watch_idle_sec={time.perf_counter() - last_progress_at:.3f}")
                return 1

            time.sleep(float(args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
