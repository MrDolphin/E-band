"""Threaded latest-frame runtime controller for FMCW radar processing."""

from dataclasses import dataclass
import queue
import threading
from time import perf_counter
from typing import Optional

from .models import RadarFrame
from .sources import RadarDataSource


@dataclass(frozen=True)
class ControllerStatus:
    """Immutable runtime values safe for GUI-thread polling."""

    running: bool
    error: Optional[str]
    shutdown_error: Optional[str]
    acquisition_time_ms: float
    processing_time_ms: float
    overruns: int
    dropped_display_frames: int


class RadarController:
    """Acquire and process CPIs on a daemon worker, retaining only the latest."""

    _SHUTDOWN_ERROR = "radar worker did not stop before timeout"
    _NOT_OPEN = "not_open"
    _OPENING = "opening"
    _OPEN = "open"
    _CLOSE_REQUESTED = "close_requested"
    _CLOSED = "closed"

    def __init__(
        self,
        source: RadarDataSource,
        processor: object,
        cpi_period_s: float,
    ) -> None:
        if cpi_period_s <= 0.0:
            raise ValueError("cpi_period_s must be positive")
        self.source = source
        self.processor = processor
        self.cpi_period_s = float(cpi_period_s)
        self._frames: queue.Queue[RadarFrame] = queue.Queue(maxsize=1)
        self._frame_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._source_condition = threading.Condition()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._cleanup_worker: Optional[threading.Thread] = None
        self._started = False
        self._source_state = self._NOT_OPEN
        self._open_in_progress = False
        self._close_started = False
        self._running = False
        self._error: Optional[str] = None
        self._shutdown_error: Optional[str] = None
        self._acquisition_time_ms = 0.0
        self._processing_time_ms = 0.0
        self._overruns = 0
        self._dropped_display_frames = 0

    def start(self) -> None:
        """Start the one-shot controller lifecycle."""
        start_error: Optional[Exception] = None
        with self._state_lock:
            if self._started:
                raise RuntimeError("radar controller has already been started")
            self._started = True
            worker = threading.Thread(
                target=self._run,
                name="fmcw-radar-controller",
                daemon=True,
            )
            self._worker = worker
            try:
                worker.start()
            except Exception as error:
                self._worker = None
                self._error = str(error)
                start_error = error
            else:
                self._running = True
        if start_error is not None:
            self._request_source_close(allow_sync_fallback=True)
            raise start_error

    def stop(self) -> None:
        """Request shutdown without waiting beyond the bounded CPI allowance."""
        deadline = perf_counter() + 2.0 * self.cpi_period_s + 1.0
        self._stop_event.set()
        with self._state_lock:
            if not self._started:
                self._started = True
            worker = self._worker

        if worker is None:
            self._request_source_close()
            return

        worker.join(timeout=max(0.0, deadline - perf_counter()))
        if worker.is_alive():
            with self._state_lock:
                self._shutdown_error = self._SHUTDOWN_ERROR
            self._request_source_close()

    def publish(self, frame: RadarFrame) -> None:
        """Publish a frame, evicting one stale display frame when necessary."""
        dropped = False
        with self._frame_lock:
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                self._frames.get_nowait()
                self._frames.put_nowait(frame)
                dropped = True
        if dropped:
            with self._state_lock:
                self._dropped_display_frames += 1

    def latest_frame(self) -> Optional[RadarFrame]:
        """Return and consume the newest available frame, or ``None``."""
        with self._frame_lock:
            try:
                return self._frames.get_nowait()
            except queue.Empty:
                return None

    @property
    def dropped_display_frames(self) -> int:
        with self._state_lock:
            return self._dropped_display_frames

    def status(self) -> ControllerStatus:
        """Return an immutable, internally consistent status snapshot."""
        with self._state_lock:
            return ControllerStatus(
                running=self._running,
                error=self._error,
                shutdown_error=self._shutdown_error,
                acquisition_time_ms=self._acquisition_time_ms,
                processing_time_ms=self._processing_time_ms,
                overruns=self._overruns,
                dropped_display_frames=self._dropped_display_frames,
            )

    def _run(self) -> None:
        open_attempted = False
        try:
            with self._source_condition:
                if self._source_state == self._CLOSE_REQUESTED:
                    return
                self._source_state = self._OPENING
                self._open_in_progress = True
            open_attempted = True
            try:
                self.source.open()
            finally:
                with self._source_condition:
                    self._open_in_progress = False
                    if self._source_state == self._OPENING:
                        self._source_state = self._OPEN
                    self._source_condition.notify_all()
            while not self._stop_event.is_set():
                acquisition_started = perf_counter()
                capture = self.source.capture()
                acquisition_s = perf_counter() - acquisition_started

                processing_started = perf_counter()
                frame = self.processor.process(capture)
                processing_s = perf_counter() - processing_started

                with self._state_lock:
                    self._acquisition_time_ms = acquisition_s * 1000.0
                    self._processing_time_ms = processing_s * 1000.0
                    if acquisition_s + processing_s > self.cpi_period_s:
                        self._overruns += 1
                self.publish(frame)
        except StopIteration:
            pass
        except Exception as error:
            self._record_primary_error(str(error))
        finally:
            if open_attempted:
                self._close_source_once()
            with self._state_lock:
                self._running = False

    def _request_source_close(self, *, allow_sync_fallback: bool = False) -> None:
        cleanup_worker: Optional[threading.Thread] = None
        with self._source_condition:
            if self._source_state == self._CLOSED or self._close_started:
                return
            self._source_state = self._CLOSE_REQUESTED
            if self._open_in_progress:
                return
            if self._cleanup_worker is not None:
                return
            cleanup_worker = threading.Thread(
                target=self._close_source_once,
                name="fmcw-radar-source-cleanup",
                daemon=True,
            )
            self._cleanup_worker = cleanup_worker
        try:
            cleanup_worker.start()
        except Exception as error:
            with self._source_condition:
                self._cleanup_worker = None
            if allow_sync_fallback:
                self._close_source_once()
            else:
                self._record_primary_error(str(error))

    def _close_source_once(self) -> None:
        with self._source_condition:
            if self._close_started or self._source_state == self._CLOSED:
                return
            if self._open_in_progress:
                self._source_state = self._CLOSE_REQUESTED
                return
            self._close_started = True
            self._source_state = self._CLOSE_REQUESTED
        try:
            self.source.close()
        except Exception as error:
            self._record_primary_error(str(error))
        finally:
            with self._source_condition:
                self._source_state = self._CLOSED
                self._source_condition.notify_all()

    def _record_primary_error(self, error: str) -> None:
        if not error:
            return
        with self._state_lock:
            if self._error is None:
                self._error = error
