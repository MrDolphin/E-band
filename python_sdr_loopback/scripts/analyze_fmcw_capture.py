from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.storage import append_metrics, load_capture, save_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze one saved FMCW IQ capture.")
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    capture = load_capture(args.capture)
    frame = FmcwProcessor(capture.config).process(capture)
    save_frame(args.output_dir, frame)
    append_metrics(args.output_dir / "metrics.jsonl", frame.diagnostics)

    print(f"active_samples={capture.config.active_samples}")
    print(f"chirp_count={capture.config.chirp_count}")
    print(f"truth_targets={len(capture.truth_targets)}")
    print(f"targets_detected={len(frame.targets)}")
    print(
        "azimuth_measured="
        + str(any(target.azimuth_deg is not None for target in frame.targets)).lower()
    )
    print(f"result={args.output_dir / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
