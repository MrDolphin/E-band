from __future__ import annotations

import argparse
import json
import sys
import zlib
from pathlib import Path


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def resolve_segment_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path
    candidate = manifest_path.parent / path.name
    if candidate.exists():
        return candidate
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect recovered SDR video segment manifest integrity.")
    parser.add_argument("--manifest-file", type=Path, default=Path("artifacts/video_segments/manifest.json"))
    parser.add_argument("--rebuild-file", type=Path, help="Optionally concatenate segments into this file.")
    args = parser.parse_args()

    if not args.manifest_file.exists():
        print(f"manifest_exists=false")
        print(f"manifest_file={args.manifest_file}")
        return 2

    manifest = json.loads(args.manifest_file.read_text(encoding="utf-8"))
    segments = list(manifest.get("segments", []))
    expected_total = int(manifest.get("file_batches_total", 0))
    expected_output_bytes = int(manifest.get("output_bytes", 0))
    expected_output_crc = int(manifest.get("output_crc32", 0))

    print(f"manifest_file={args.manifest_file}")
    print(f"manifest_segments={len(segments)}")
    print(f"manifest_file_batches_total={expected_total}")
    print(f"manifest_file_ok={str(bool(manifest.get('file_ok'))).lower()}")

    rebuilt = bytearray()
    segment_errors = 0
    expected_offset = 0
    seen_indexes: set[int] = set()

    for position, segment in enumerate(segments, start=1):
        index = int(segment.get("index", 0))
        raw_path = str(segment.get("path", ""))
        expected_size = int(segment.get("size", -1))
        expected_crc = int(segment.get("crc32", -1))
        expected_segment_offset = int(segment.get("offset", -1))
        path = resolve_segment_path(args.manifest_file, raw_path)

        exists = path.exists()
        size_ok = False
        crc_ok = False
        offset_ok = expected_segment_offset == expected_offset
        index_ok = index == position and index not in seen_indexes
        data = b""
        if exists:
            data = path.read_bytes()
            size_ok = len(data) == expected_size
            crc_ok = crc32(data) == expected_crc
            rebuilt.extend(data)
            expected_offset += len(data)

        segment_ok = exists and size_ok and crc_ok and offset_ok and index_ok
        if not segment_ok:
            segment_errors += 1
        seen_indexes.add(index)
        print(
            "segment="
            f"{index} path={path} exists={str(exists).lower()} "
            f"size_ok={str(size_ok).lower()} crc_ok={str(crc_ok).lower()} "
            f"offset_ok={str(offset_ok).lower()} index_ok={str(index_ok).lower()}"
        )

    rebuilt_bytes = bytes(rebuilt)
    rebuilt_crc = crc32(rebuilt_bytes)
    rebuilt_size_ok = len(rebuilt_bytes) == expected_output_bytes
    rebuilt_crc_ok = rebuilt_crc == expected_output_crc
    segment_count_ok = len(segments) == expected_total
    inspect_ok = segment_errors == 0 and segment_count_ok and rebuilt_size_ok and rebuilt_crc_ok

    if args.rebuild_file:
        args.rebuild_file.parent.mkdir(parents=True, exist_ok=True)
        args.rebuild_file.write_bytes(rebuilt_bytes)
        print(f"rebuilt_file={args.rebuild_file}")

    print(f"segment_count_ok={str(segment_count_ok).lower()}")
    print(f"segment_errors={segment_errors}")
    print(f"rebuilt_bytes={len(rebuilt_bytes)}")
    print(f"rebuilt_crc32={rebuilt_crc}")
    print(f"expected_output_bytes={expected_output_bytes}")
    print(f"expected_output_crc32={expected_output_crc}")
    print(f"rebuilt_size_ok={str(rebuilt_size_ok).lower()}")
    print(f"rebuilt_crc_ok={str(rebuilt_crc_ok).lower()}")
    print(f"inspect_ok={str(inspect_ok).lower()}")
    return 0 if inspect_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
