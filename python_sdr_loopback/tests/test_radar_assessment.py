import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sdr_loopback.radar.assessment import assess_frame, append_position_candidate
from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.simulator import simulate_capture


class PositionAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig(
            sample_rate_hz=2e6,
            bandwidth_hz=20e6,
            active_time_s=64e-6,
            idle_time_s=16e-6,
            chirp_count=16,
            range_fft_size=128,
            doppler_fft_size=16,
        )

    def test_target_candidate_is_ranked_above_empty_frame_and_is_serializable(self):
        processor = FmcwProcessor(self.config)
        empty = processor.process(simulate_capture(self.config, seed=1))
        target = processor.process(
            simulate_capture(
                self.config,
                (SyntheticTarget("corner", 22.5, 0.0, amplitude=0.1, snr_db=40.0),),
                seed=2,
            )
        )

        empty_assessment = assess_frame(empty)
        assessment = assess_frame(
            target, label="8m-azimuth-0", measured_range_m=8.0, notes="corner"
        )

        self.assertGreater(assessment.quality_score, empty_assessment.quality_score)
        self.assertEqual(assessment.label, "8m-azimuth-0")
        self.assertEqual(assessment.measured_range_m, 8.0)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.jsonl"
            append_position_candidate(output, assessment)
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["label"], "8m-azimuth-0")
        self.assertIn("quality_score", saved)


if __name__ == "__main__":
    unittest.main()
