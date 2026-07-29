from statistics import median
from time import perf_counter
import unittest

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.simulator import simulate_capture


class RadarPerformanceTests(unittest.TestCase):
    def test_default_cpi_median_processing_time_is_under_50_ms(self):
        config = RadarConfig()
        processor = FmcwProcessor(config)

        processor.process(simulate_capture(config, seed=0))
        timings_ms = []
        for seed in range(1, 6):
            capture = simulate_capture(config, seed=seed)
            started_at = perf_counter()
            processor.process(capture)
            timings_ms.append((perf_counter() - started_at) * 1_000.0)

        self.assertLess(median(timings_ms), 50.0, timings_ms)


if __name__ == "__main__":
    unittest.main()
