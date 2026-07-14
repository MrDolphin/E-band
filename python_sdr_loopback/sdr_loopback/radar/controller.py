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
    acquisition_time_ms: float
    processing_time_ms: float
    overruns: int
    dropped_display_frames: int


class RadarController:
    """Acquire and process CPIs on a daemon worker, retaining only the latest."""

    _SHUTDOWN_ERROR = "radar worker did not stop before timeout"

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
        self._close_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._started = False
        self._source_closed = False
        self._running = False
        self._error: Optional[str] = None
        self._acquisition_time_ms = 0.0
        self._processing_time_ms = 0.0
        self._overruns = 0
        self._dropped_display_frames = 0

    def start(self) -> None:
        """Start the one-shot controller lifecycle."""
        with self._state_lock:
            if self._started:
                raise RuntimeError("radar controller has already been started")
            self._started = True
            self._running = True
            worker = threading.Thread(
                target=self._run,
                name="fmcw-radar-controller",
                daemon=True,
            )
            self._worker = worker
            worker.start()

    def stop(self) -> None:
        """Request shutdown without waiting beyond the bounded CPI allowance."""
        self._stop_event.set()
        with self._state_lock:
            if not self._started:
                self._started = True
            worker = self._worker

        if worker is None:
            self._record_close_error(self._close_source_once())
            with self._state_lock:
                self._running = False
            return

        worker.join(timeout=2.0 * self.cpi_period_s + 1.0)
        if worker.is_alive():
            with self._state_lock:
                self._error = self._SHUTDOWN_ERROR
            self._record_close_error(self._close_source_once())
            worker.join(timeout=min(self.cpi_period_s, 0.05))
            with self._state_lock:
                self._running = worker.is_alive()

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
                acquisition_time_ms=self._acquisition_time_ms,
                processing_time_ms=self._processing_time_ms,
                overruns=self._overruns,
                dropped_display_frames=self._dropped_display_frames,
            )

    def _run(self) -> None:
        try:
            self.source.open()
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
            with self._state_lock:
                if self._error is None:
                    self._error = str(error)
        finally:
            self._record_close_error(self._close_source_once())
            with self._state_lock:
                self._running = False

    def _close_source_once(self) -> Optional[str]:
        with self._close_lock:
            if self._source_closed:
                return None
            self._source_closed = True
            try:
                self.source.close()
            except Exception as error:
                return str(error)
        return None

    def _record_close_error(self, error: Optional[str]) -> None:
        if error is None:
            return
        with self._state_lock:
            if self._error is None:
                self._error = error
