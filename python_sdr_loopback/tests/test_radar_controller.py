import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

from sdr_loopback.radar.controller import RadarController


class FakeSource:
    def __init__(self, captures=()):
        self.captures = iter(captures)
        self.open_calls = 0
        self.close_calls = 0
        self.closed = threading.Event()

    def open(self):
        self.open_calls += 1

    def capture(self):
        return next(self.captures)

    def close(self):
        self.close_calls += 1
        self.closed.set()


class FakeProcessor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.called = threading.Event()

    def process(self, capture):
        self.called.set()
        if self.error is not None:
            raise self.error
        return self.result


class BlockingSource(FakeSource):
    def __init__(self, *, close_unblocks=True):
        super().__init__()
        self.close_unblocks = close_unblocks
        self.capture_started = threading.Event()
        self.release_capture = threading.Event()

    def capture(self):
        self.capture_started.set()
        self.release_capture.wait()
        raise StopIteration

    def close(self):
        super().close()
        if self.close_unblocks:
            self.release_capture.set()


class RadarControllerTests(unittest.TestCase):
    def test_cpi_period_must_be_positive(self):
        source = FakeSource()
        processor = FakeProcessor()
        with self.assertRaisesRegex(ValueError, "cpi_period_s must be positive"):
            RadarController(source, processor, 0.0)

    def test_start_opens_once_processes_and_closes_on_exhaustion(self):
        frame = SimpleNamespace(frame_index=1)
        source = FakeSource([object()])
        processor = FakeProcessor(result=frame)
        controller = RadarController(source, processor, 0.1)
        controller.start()
        self.assertTrue(source.closed.wait(0.5))
        controller.stop()

        self.assertEqual(source.open_calls, 1)
        self.assertEqual(source.close_calls, 1)
        self.assertIs(controller.latest_frame(), frame)
        status = controller.status()
        self.assertFalse(status.running)
        self.assertIsNone(status.error)
        self.assertGreaterEqual(status.acquisition_time_ms, 0.0)
        self.assertGreaterEqual(status.processing_time_ms, 0.0)
        with self.assertRaises(FrozenInstanceError):
            status.running = True
        with self.assertRaisesRegex(RuntimeError, "already been started"):
            controller.start()

    def test_concurrent_start_and_stop_never_join_an_unstarted_worker(self):
        real_thread = threading.Thread
        start_entered = threading.Event()
        allow_start = threading.Event()
        errors = []

        class GatedThread(real_thread):
            def start(self):
                start_entered.set()
                allow_start.wait(0.5)
                return super().start()

        source = FakeSource()
        controller = RadarController(source, FakeProcessor(), 0.01)

        def call(action):
            try:
                action()
            except Exception as error:
                errors.append(error)

        with patch("sdr_loopback.radar.controller.threading.Thread", GatedThread):
            starter = real_thread(target=call, args=(controller.start,))
            stopper = real_thread(target=call, args=(controller.stop,))
            starter.start()
            self.assertTrue(start_entered.wait(0.5))
            stopper.start()
            allow_start.set()
            starter.join(0.5)
            stopper.join(0.5)

        self.assertEqual(errors, [])
        self.assertEqual(source.open_calls, 1)
        self.assertEqual(source.close_calls, 1)

    def test_worker_exception_becomes_status_and_stop_still_closes(self):
        source = FakeSource([object()])
        processor = FakeProcessor(error=RuntimeError("processor exploded"))
        controller = RadarController(source, processor, 0.1)
        controller.start()
        self.assertTrue(source.closed.wait(0.5))
        controller.stop()

        status = controller.status()
        self.assertFalse(status.running)
        self.assertEqual(status.error, "processor exploded")
        self.assertEqual(source.close_calls, 1)

    def test_publish_replaces_stale_frame_and_counts_drops(self):
        controller = RadarController(FakeSource(), FakeProcessor(), 0.1)
        frames = [SimpleNamespace(frame_index=index) for index in range(1, 4)]
        for frame in frames:
            controller.publish(frame)

        self.assertEqual(controller.latest_frame().frame_index, 3)
        self.assertEqual(controller.dropped_display_frames, 2)
        self.assertEqual(controller.status().dropped_display_frames, 2)
        self.assertIsNone(controller.latest_frame())

    def test_overrun_counts_acquisition_plus_processing_time(self):
        frame = SimpleNamespace(frame_index=1)
        source = FakeSource([object()])
        processor = FakeProcessor(result=frame)
        controller = RadarController(source, processor, 1e-9)

        controller.start()
        self.assertTrue(source.closed.wait(0.5))

        status = controller.status()
        self.assertEqual(status.overruns, 1)
        self.assertGreater(
            status.acquisition_time_ms + status.processing_time_ms,
            controller.cpi_period_s * 1000.0,
        )

    def test_stop_timeout_closes_once_and_reports_stable_shutdown_error(self):
        source = BlockingSource(close_unblocks=False)
        controller = RadarController(source, FakeProcessor(), 0.01)
        controller.start()
        self.assertTrue(source.capture_started.wait(0.5))

        started = time.perf_counter()
        controller.stop()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.2)
        self.assertEqual(source.close_calls, 1)
        first_status = controller.status()
        self.assertTrue(first_status.running)
        self.assertEqual(first_status.error, "radar worker did not stop before timeout")
        source.release_capture.set()
        controller.stop()
        self.assertFalse(controller.status().running)
        self.assertEqual(source.close_calls, 1)
        self.assertEqual(controller.status().error, first_status.error)

    def test_stop_before_start_is_safe_and_closes_once(self):
        source = FakeSource()
        controller = RadarController(source, FakeProcessor(), 0.1)

        controller.stop()
        controller.stop()

        self.assertEqual(source.open_calls, 0)
        self.assertEqual(source.close_calls, 1)
        self.assertFalse(controller.status().running)
