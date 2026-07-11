from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL record: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSONL record must be an object")
            records.append(value)
    return records


def numeric_values(records: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
        elif isinstance(value, str):
            try:
                values.append(float(value))
            except ValueError:
                continue
    return values


def int_values(records: list[dict[str, Any]], key: str) -> list[int]:
    return [int(value) for value in numeric_values(records, key)]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def print_float_summary(prefix: str, values: list[float], digits: int = 3) -> None:
    if not values:
        print(f"{prefix}_count=0")
        return
    print(f"{prefix}_count={len(values)}")
    print(f"{prefix}_avg={statistics.mean(values):.{digits}f}")
    print(f"{prefix}_min={min(values):.{digits}f}")
    print(f"{prefix}_p50={statistics.median(values):.{digits}f}")
    print(f"{prefix}_p90={percentile(values, 0.90):.{digits}f}")
    print(f"{prefix}_max={max(values):.{digits}f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize SDR stream metrics JSONL output.")
    parser.add_argument("metrics_file", type=Path)
    args = parser.parse_args()

    if not args.metrics_file.exists():
        print(f"metrics_exists=false")
        print(f"metrics_file={args.metrics_file}")
        return 2

    try:
        records = load_records(args.metrics_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ok_key = "chunk_ok" if any("chunk_ok" in record for record in records) else "batch_file_ok"
    item_label = "chunk" if ok_key == "chunk_ok" else "batch"
    ok_records = [record for record in records if bool(record.get(ok_key))]
    failed_records = [record for record in records if not bool(record.get(ok_key))]
    capture_attempts = int_values(records, "capture_attempts")
    context_recreates = int_values(records, "context_recreates_this_batch")
    missing_chunks = [
        len(record.get("missing_chunks", []))
        for record in records
        if isinstance(record.get("missing_chunks", []), list)
    ]

    print(f"metrics_file={args.metrics_file}")
    print(f"{item_label}_count={len(records)}")
    print(f"{item_label}_ok_count={len(ok_records)}")
    print(f"{item_label}_failed_count={len(failed_records)}")
    print(f"capture_attempts_total={sum(capture_attempts)}")
    print(f"capture_attempts_avg={statistics.mean(capture_attempts) if capture_attempts else 0.0:.3f}")
    print(f"context_recreates_total={sum(context_recreates)}")
    print(f"context_recreates_max={max(context_recreates) if context_recreates else 0}")
    print(f"missing_chunks_total={sum(missing_chunks)}")
    print_float_summary("chunk_elapsed_sec", numeric_values(records, "chunk_elapsed_sec"))
    print_float_summary("batch_elapsed_sec", numeric_values(records, "batch_elapsed_sec"))
    print_float_summary("sdr_capture_elapsed_sec", numeric_values(records, "sdr_capture_elapsed_sec"))
    print_float_summary("decode_elapsed_sec", numeric_values(records, "decode_elapsed_sec"))
    print_float_summary("rx_rms_dbfs", numeric_values(records, "rx_rms_dbfs"), digits=2)
    print_float_summary("stream_goodput_bps", numeric_values(records, "stream_goodput_bps"), digits=0)

    capture_time = sum(numeric_values(records, "sdr_capture_elapsed_sec"))
    decode_time = sum(numeric_values(records, "decode_elapsed_sec"))
    if capture_time > decode_time * 2.0:
        print("bottleneck_hint=sdr_capture")
    elif decode_time > capture_time * 2.0:
        print("bottleneck_hint=decode")
    else:
        print("bottleneck_hint=mixed")
    print(f"metrics_ok={str(len(records) > 0 and not failed_records).lower()}")
    return 0 if records and not failed_records else 1


if __name__ == "__main__":
    raise SystemExit(main())
