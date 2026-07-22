import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

from sdr_loopback.radar.controller import RadarController
from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig
from sdr_loopback.radar.synchronizer import ChirpSyncDiagnostics, ChirpSyncError


class FakeSource:
    def __init__(self, captures=()):
        self.captures = iter(captures)
        self.open_calls = 0
        self.close_calls = 0
        self.closed = threading.Event()
        self.recover_calls = 0

    def open(self):
        self.open_calls += 1

    def capture(self):
        return next(self.captures)

    def close(self):
        self.close_calls += 1
        self.closed.set()

    def recover(self):
        self.recover_calls += 1


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


class DelayedOpenSource(FakeSource):
    def __init__(self):
        super().__init__()
        self.actions = []
        self.open_started = threading.Event()
        self.allow_open = threading.Event()

    def open(self):
        self.open_calls += 1
        self.actions.append("open-start")
        self.open_started.set()
        self.allow_open.wait()
        self.actions.append("open-end")

    def close(self):
        self.actions.append("close")
        super().close()


class BlockingCaptureAndCloseSource(FakeSource):
    def __init__(self):
        super().__init__()
        self.capture_started = threading.Event()
        self.release_capture = threading.Event()
        self.close_started = threading.Event()
        self.release_close = threading.Event()

    def capture(self):
        self.capture_started.set()
        self.release_capture.wait()
        raise StopIteration

    def close(self):
        self.close_calls += 1
        self.close_started.set()
        self.release_close.wait()
        self.closed.set()
        self.release_capture.set()


class BlockingCloseSource(FakeSource):
    def __init__(self, captures=()):
        super().__init__(captures)
        self.close_started = threading.Event()
        self.release_close = threading.Event()

    def close(self):
        self.close_calls += 1
        self.close_started.set()
        self.release_close.wait()
        self.closed.set()


class FailOnceCloseSource(FakeSource):
    def close(self):
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("transient close failure")
        self.closed.set()


class FailTwiceCloseSource(FakeSource):
    def close(self):
        self.close_calls += 1
        if self.close_calls <= 2:
            raise OSError("persistent close failure")
        self.closed.set()


class RadarControllerTests(unittest.TestCase):
    def test_cpi_period_must_be_positive(self):
        source = FakeSource()
        processor = FakeProcessor()
        with self.assertRaisesRegex(ValueError, "cpi_period_s must be positive"):
            RadarController(source, processor, 0.0)

    def test_source_recovery_limits_must_be_nonnegative_integers(self):
        for name in ("recover_after_processing_errors", "max_source_recoveries"):
            for value in (True, -1, 1.5):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, "nonnegative integers"):
                        RadarController(
                            FakeSource(),
                            FakeProcessor(),
                            0.01,
                            **{name: value},
                        )

    def test_e310_stop_timeout_eventually_cleans_after_blocking_rx_returns(self):
        from tests.test_radar_sources import FakeAd9361

        device = FakeAd9361("ip:192.168.1.10")
        entered = threading.Event()
        release = threading.Event()
        original_rx = device.rx

        def blocking_rx():
            entered.set()
            release.wait(timeout=3.0)
            return original_rx()

        device.rx = blocking_rx
        adi = SimpleNamespace(ad9361=lambda uri: device)
        source = E310CpiSource(
            RadarConfig(),
            E310RadioConfig(pre_tx_settle_s=0.0, settle_s=0.0),
            adi_module=adi,
        )
        controller = RadarController(source, FakeProcessor(result=object()), 0.001)
        controller.start()
        self.assertTrue(entered.wait(0.5))

        controller.stop()
        self.assertEqual(
            controller.status().shutdown_error,
            "radar worker did not stop before timeout",
        )
        release.set()
        deadline = time.monotonic() + 1.0
        while source._sdr is not None and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertIsNone(source._sdr)
        self.assertGreaterEqual(device.dds_disable_count, 1)
        self.assertGreaterEqual(device.tx_destroy_count, 1)
        self.assertGreaterEqual(device.rx_destroy_count, 1)

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

    def test_transient_close_failure_is_retried_before_shutdown_completes(self):
        source = FailOnceCloseSource()
        controller = RadarController(source, FakeProcessor(), 0.01)

        controller.start()

        self.assertTrue(source.closed.wait(0.5))
        deadline = time.monotonic() + 0.5
        while controller.status().running and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(source.close_calls, 2)
        self.assertFalse(controller.status().running)

    def test_recoverable_processing_error_skips_frame_and_continues(self):
        frame = SimpleNamespace(frame_index=2)
        source = FakeSource([object(), object()])

        class Processor:
            def __init__(self):
                self.calls = 0

            def process(self, _capture):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError("temporary sync loss")
                return frame

        controller = RadarController(
            source,
            Processor(),
            0.01,
            recoverable_processing_errors=(ValueError,),
        )

        controller.start()

        self.assertTrue(source.closed.wait(0.5))
        self.assertIs(controller.latest_frame(), frame)
        self.assertIsNone(controller.status().error)
        self.assertEqual(source.close_calls, 1)

    def test_consecutive_processing_errors_trigger_one_bounded_source_recovery(self):
        frame = SimpleNamespace(frame_index=3)
        source = FakeSource([object() for _ in range(7)])

        class Processor:
            def __init__(self):
                self.calls = 0

            def process(self, _capture):
                self.calls += 1
                if self.calls <= 6:
                    raise ValueError("temporary sync loss")
                return frame

        controller = RadarController(
            source,
            Processor(),
            0.01,
            recoverable_processing_errors=(ValueError,),
            recover_after_processing_errors=3,
            max_source_recoveries=1,
        )

        controller.start()

        self.assertTrue(source.closed.wait(0.5))
        self.assertEqual(source.recover_calls, 1)
        self.assertIs(controller.latest_frame(), frame)

    def test_recovery_status_reports_exhaustion_and_sync_measurements(self):
        diagnostics = ChirpSyncDiagnostics(
            failed_metric="min_correlation",
            min_correlation=0.03,
            mean_correlation=0.06,
            periodic_coherence=0.004,
            idle_to_active_db=-0.5,
            min_correlation_threshold=0.05,
            mean_correlation_threshold=0.08,
            periodic_coherence_threshold=0.0075,
        )
        sync_error = ChirpSyncError("sync failed", diagnostics)
        source = FakeSource([object() for _ in range(6)])
        source_diagnostics = SimpleNamespace(session_attempt=2, phase="capturing")
        source.diagnostic_status = lambda: source_diagnostics
        event = SimpleNamespace(phase="tx_uploaded")
        source.drain_diagnostic_events = lambda: (event,)
        controller = RadarController(
            source,
            FakeProcessor(error=sync_error),
            0.01,
            recoverable_processing_errors=(ChirpSyncError,),
            recover_after_processing_errors=3,
            max_source_recoveries=1,
        )

        controller.start()

        self.assertTrue(source.closed.wait(0.5))
        status = controller.status()
        self.assertEqual(status.consecutive_processing_errors, 3)
        self.assertEqual(status.source_recoveries, 1)
        self.assertEqual(status.max_source_recoveries, 1)
        self.assertTrue(status.recovery_exhausted)
        self.assertIs(status.sync_diagnostics, diagnostics)
        self.assertIs(status.source_diagnostics, source_diagnostics)
        self.assertEqual(controller.drain_diagnostic_events(), (event,))

    def test_cleanup_state_stays_incomplete_until_failed_close_is_retried(self):
        source = FailTwiceCloseSource()
        controller = RadarController(source, FakeProcessor(), 0.01)
        controller.start()
        deadline = time.monotonic() + 0.5
        while controller.status().running and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertFalse(controller.status().running)
        self.assertFalse(controller.status().cleanup_complete)
        self.assertEqual(source.close_calls, 2)

        controller.stop()

        self.assertTrue(source.closed.wait(0.5))
        self.assertTrue(controller.status().cleanup_complete)
        self.assertEqual(source.close_calls, 3)

    def test_concurrent_start_and_stop_never_join_an_unstarted_worker(self):
        real_thread = threading.Thread
        start_entered = threading.Event()
        stop_at_state_lock = threading.Event()
        allow_start = threading.Event()
        errors = []

        class ObservedLock:
            def __init__(self, lock):
                self.lock = lock
                self.metadata_lock = threading.Lock()
                self._owner = None

            @property
            def owner(self):
                with self.metadata_lock:
                    return self._owner

            def __enter__(self):
                if threading.current_thread().name == "controlled-stopper":
                    with self.metadata_lock:
                        if self._owner == "controlled-starter":
                            stop_at_state_lock.set()
                self.lock.acquire()
                with self.metadata_lock:
                    self._owner = threading.current_thread().name
                return self

            def __exit__(self, *args):
                with self.metadata_lock:
                    self._owner = None
                self.lock.release()

        class GatedThread(real_thread):
            def start(self):
                start_entered.set()
                if not allow_start.wait(1.0):
                    raise AssertionError("test did not release worker start")
                return super().start()

        source = FakeSource()
        controller = RadarController(source, FakeProcessor(), 0.01)
        controller._state_lock = ObservedLock(controller._state_lock)

        def call(action):
            try:
                action()
            except Exception as error:
                errors.append(error)

        with patch("sdr_loopback.radar.controller.threading.Thread", GatedThread):
            starter = real_thread(
                target=call,
                args=(controller.start,),
                name="controlled-starter",
            )
            stopper = real_thread(
                target=call,
                args=(controller.stop,),
                name="controlled-stopper",
            )
            starter.start()
            self.assertTrue(start_entered.wait(0.5))
            stopper.start()
            try:
                self.assertTrue(stop_at_state_lock.wait(0.5))
                self.assertEqual(
                    controller._state_lock.owner,
                    "controlled-starter",
                )
            finally:
                allow_start.set()
                starter.join(0.5)
                stopper.join(0.5)

        self.assertEqual(errors, [])
        self.assertEqual(source.open_calls, 1)
        self.assertEqual(source.close_calls, 1)

    def test_timeout_cannot_restore_running_after_worker_finishes(self):
        real_thread = threading.Thread
        source = BlockingSource(close_unblocks=False)

        class CapturedAliveThread:
            def __init__(self, *, target, name, daemon):
                self.inner = real_thread(target=target, name=name, daemon=daemon)

            def start(self):
                self.inner.start()

            def join(self, timeout=None):
                return None

            def is_alive(self):
                captured_alive = self.inner.is_alive()
                source.release_capture.set()
                self.inner.join(0.5)
                return captured_alive

        controller = RadarController(source, FakeProcessor(), 0.01)
        with patch(
            "sdr_loopback.radar.controller.threading.Thread",
            CapturedAliveThread,
        ):
            controller.start()
            self.assertTrue(source.capture_started.wait(0.5))
            controller.stop()

        self.assertFalse(controller.status().running)
        self.assertEqual(source.close_calls, 1)

    def test_stop_during_open_closes_only_after_open_completes(self):
        source = DelayedOpenSource()
        controller = RadarController(source, FakeProcessor(), 0.001)
        controller.start()
        self.assertTrue(source.open_started.wait(0.5))

        try:
            controller.stop()
            self.assertEqual(source.close_calls, 0)
        finally:
            source.allow_open.set()

        self.assertTrue(source.closed.wait(0.5))
        controller.stop()
        self.assertEqual(source.actions, ["open-start", "open-end", "close"])
        self.assertEqual(source.close_calls, 1)

    def test_thread_start_failure_is_terminal_and_closes_once(self):
        source = FakeSource()
        controller = RadarController(source, FakeProcessor(), 0.01)

        with patch(
            "sdr_loopback.radar.controller.threading.Thread.start",
            side_effect=RuntimeError("thread launch failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread launch failed"):
                controller.start()

        self.assertTrue(source.closed.wait(0.5))
        status = controller.status()
        self.assertFalse(status.running)
        self.assertEqual(status.error, "thread launch failed")
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

    def test_overrun_uses_acquisition_plus_processing_and_not_equality(self):
        source = FakeSource([object(), object()])
        processor = FakeProcessor(result=SimpleNamespace(frame_index=1))
        controller = RadarController(source, processor, 1.0)

        clock_values = [0.0, 0.4, 0.4, 1.0, 1.0, 1.6, 1.6, 2.1, 2.1]
        with patch(
            "sdr_loopback.radar.controller.perf_counter",
            side_effect=clock_values,
        ):
            controller.start()
            self.assertTrue(source.closed.wait(0.5))

        status = controller.status()
        self.assertEqual(status.overruns, 1)
        self.assertAlmostEqual(status.acquisition_time_ms, 600.0)
        self.assertAlmostEqual(status.processing_time_ms, 500.0)

    def test_stop_deadline_does_not_wait_for_blocking_close(self):
        source = BlockingCaptureAndCloseSource()
        controller = RadarController(source, FakeProcessor(), 0.01)
        controller.start()
        self.assertTrue(source.capture_started.wait(0.5))
        stop_done = threading.Event()
        elapsed = []

        def stop_controller():
            started = time.perf_counter()
            controller.stop()
            elapsed.append(time.perf_counter() - started)
            stop_done.set()

        stopper = threading.Thread(target=stop_controller)
        stopper.start()
        try:
            self.assertTrue(stop_done.wait(1.2))
            self.assertLess(elapsed[0], 1.15)
            self.assertTrue(source.close_started.wait(0.2))
        finally:
            source.release_close.set()
            source.release_capture.set()
            stopper.join(0.5)
        self.assertTrue(source.closed.wait(0.5))
        controller.stop()
        self.assertEqual(source.close_calls, 1)

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
        self.assertIsNone(first_status.error)
        self.assertEqual(
            first_status.shutdown_error,
            "radar worker did not stop before timeout",
        )
        source.release_capture.set()
        controller.stop()
        self.assertFalse(controller.status().running)
        self.assertEqual(source.close_calls, 1)
        self.assertEqual(controller.status().shutdown_error, first_status.shutdown_error)

    def test_worker_error_survives_shutdown_timeout_while_close_blocks(self):
        source = BlockingCloseSource([object()])
        processor = FakeProcessor(error=RuntimeError("processor exploded"))
        controller = RadarController(source, processor, 0.01)
        controller.start()
        self.assertTrue(source.close_started.wait(0.5))
        stop_done = threading.Event()
        stopper = threading.Thread(target=lambda: (controller.stop(), stop_done.set()))
        stopper.start()

        try:
            self.assertTrue(stop_done.wait(1.2))
            status = controller.status()
            self.assertEqual(status.error, "processor exploded")
            self.assertEqual(
                status.shutdown_error,
                "radar worker did not stop before timeout",
            )
        finally:
            source.release_close.set()
            stopper.join(0.5)

        controller.stop()
        self.assertFalse(controller.status().running)
        self.assertEqual(source.close_calls, 1)

    def test_stop_before_start_is_safe_and_closes_once(self):
        source = FakeSource()
        controller = RadarController(source, FakeProcessor(), 0.1)

        controller.stop()
        controller.stop()

        self.assertTrue(source.closed.wait(0.5))
        self.assertEqual(source.open_calls, 0)
        self.assertEqual(source.close_calls, 1)
        self.assertFalse(controller.status().running)
