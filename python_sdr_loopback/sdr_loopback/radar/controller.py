"""Threaded latest-frame runtime controller for FMCW radar processing."""

from dataclasses import dataclass
import math
import queue
import threading
from time import monotonic, perf_counter
from typing import Optional

from .models import RadarFrame
from .sources import RadarDataSource


@dataclass(frozen=True)
class ControllerStatus:
    """Immutable runtime values safe for GUI-thread polling."""

    running: bool
    cleanup_complete: bool
    error: Optional[str]
    processing_error: Optional[str]
    shutdown_error: Optional[str]
    acquisition_time_ms: float
    processing_time_ms: float
    overruns: int
    dropped_display_frames: int
    consecutive_processing_errors: int
    source_recoveries: int
    max_source_recoveries: int
    recovery_exhausted: bool
    sync_diagnostics: object | None
    source_diagnostics: object | None
    alignment_duration_s: float
    alignment_active: bool
    alignment_remaining_s: float
    ever_synchronized: bool
    alignment_best_sync_score: float
    last_success_age_s: float | None


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
        *,
        recoverable_processing_errors: tuple[type[Exception], ...] = (),
        recover_after_processing_errors: int = 0,
        max_source_recoveries: int = 0,
        alignment_duration_s: float = 0.0,
    ) -> None:
        if cpi_period_s <= 0.0:
            raise ValueError("cpi_period_s must be positive")
        if (
            type(recover_after_processing_errors) is not int
            or type(max_source_recoveries) is not int
            or recover_after_processing_errors < 0
            or max_source_recoveries < 0
        ):
            raise ValueError("source recovery limits must be nonnegative integers")
        if (
            isinstance(alignment_duration_s, bool)
            or not isinstance(alignment_duration_s, (int, float))
            or not math.isfinite(alignment_duration_s)
            or alignment_duration_s < 0.0
        ):
            raise ValueError("alignment_duration_s must be a nonnegative number")
        self.source = source
        self.processor = processor
        self.cpi_period_s = float(cpi_period_s)
        self._recoverable_processing_errors = recoverable_processing_errors
        self._recover_after_processing_errors = recover_after_processing_errors
        self._max_source_recoveries = max_source_recoveries
        self._alignment_duration_s = float(alignment_duration_s)
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
        self._processing_error: Optional[str] = None
        self._shutdown_error: Optional[str] = None
        self._acquisition_time_ms = 0.0
        self._processing_time_ms = 0.0
        self._overruns = 0
        self._dropped_display_frames = 0
        self._consecutive_processing_errors = 0
        self._source_recoveries = 0
        self._recovery_exhausted = False
        self._sync_diagnostics = None
        self._alignment_deadline: float | None = None
        self._ever_synchronized = False
        self._alignment_best_sync_score = 0.0
        self._last_success_at: float | None = None

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
        else:
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
        diagnostic_status = getattr(self.source, "diagnostic_status", None)
        source_diagnostics = (
            diagnostic_status() if diagnostic_status is not None else None
        )
        with self._source_condition:
            cleanup_complete = self._source_state == self._CLOSED
        now = monotonic()
        with self._state_lock:
            alignment_remaining_s = max(
                0.0,
                (self._alignment_deadline or now) - now,
            )
            return ControllerStatus(
                running=self._running,
                cleanup_complete=cleanup_complete,
                error=self._error or self._processing_error,
                processing_error=self._processing_error,
                shutdown_error=self._shutdown_error,
                acquisition_time_ms=self._acquisition_time_ms,
                processing_time_ms=self._processing_time_ms,
                overruns=self._overruns,
                dropped_display_frames=self._dropped_display_frames,
                consecutive_processing_errors=self._consecutive_processing_errors,
                source_recoveries=self._source_recoveries,
                max_source_recoveries=self._max_source_recoveries,
                recovery_exhausted=self._recovery_exhausted,
                sync_diagnostics=self._sync_diagnostics,
                source_diagnostics=source_diagnostics,
                alignment_duration_s=self._alignment_duration_s,
                alignment_active=alignment_remaining_s > 0.0,
                alignment_remaining_s=alignment_remaining_s,
                ever_synchronized=self._ever_synchronized,
                alignment_best_sync_score=self._alignment_best_sync_score,
                last_success_age_s=(
                    None
                    if self._last_success_at is None
                    else max(0.0, now - self._last_success_at)
                ),
            )

    def drain_diagnostic_events(self) -> tuple[object, ...]:
        """Consume optional hardware-source transition events for GUI logging."""
        drain = getattr(self.source, "drain_diagnostic_events", None)
        return tuple(drain()) if drain is not None else ()

    def _run(self) -> None:
        open_attempted = False
        consecutive_processing_errors = 0
        source_recoveries = 0
        ever_synchronized = False
        try:
            with self._source_condition:
                if self._source_state == self._CLOSE_REQUESTED:
                    return
                self._source_state = self._OPENING
                self._open_in_progress = True
            open_attempted = True
            try:
                self.source.open()
                alignment_deadline = (
                    monotonic() + self._alignment_duration_s
                    if self._alignment_duration_s > 0.0
                    else None
                )
                with self._state_lock:
                    self._alignment_deadline = alignment_deadline
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
                try:
                    frame = self.processor.process(capture)
                except self._recoverable_processing_errors as error:
                    note_sync_failure = getattr(
                        self.processor,
                        "note_background_calibration_sync_failure",
                        None,
                    )
                    if callable(note_sync_failure):
                        note_sync_failure()
                    sync_diagnostics = getattr(error, "diagnostics", None)
                    failed_sync_score = float(
                        getattr(sync_diagnostics, "min_correlation", 0.0)
                    )
                    if not math.isfinite(failed_sync_score):
                        failed_sync_score = 0.0
                    consecutive_processing_errors += 1
                    threshold_reached = (
                        self._recover_after_processing_errors > 0
                        and consecutive_processing_errors
                        >= self._recover_after_processing_errors
                    )
                    can_recover = (
                        getattr(self.source, "recover", None) is not None
                        and threshold_reached
                        and source_recoveries < self._max_source_recoveries
                        and not ever_synchronized
                        and not (
                            alignment_deadline is not None
                            and monotonic() < alignment_deadline
                        )
                    )
                    with self._state_lock:
                        self._processing_error = str(error)
                        self._consecutive_processing_errors = (
                            consecutive_processing_errors
                        )
                        self._source_recoveries = source_recoveries
                        self._recovery_exhausted = (
                            threshold_reached
                            and source_recoveries >= self._max_source_recoveries
                            and self._max_source_recoveries > 0
                        )
                        self._sync_diagnostics = sync_diagnostics
                        self._alignment_best_sync_score = max(
                            self._alignment_best_sync_score,
                            failed_sync_score,
                        )
                    recovery = getattr(self.source, "recover", None)
                    if can_recover:
                        recovery()
                        source_recoveries += 1
                        consecutive_processing_errors = 0
                        with self._state_lock:
                            self._consecutive_processing_errors = 0
                            self._source_recoveries = source_recoveries
                            self._recovery_exhausted = False
                    continue
                processing_s = perf_counter() - processing_started

                with self._state_lock:
                    self._processing_error = None
                    self._consecutive_processing_errors = 0
                    self._recovery_exhausted = False
                    self._sync_diagnostics = None
                    self._ever_synchronized = True
                    sync_score = float(
                        getattr(getattr(frame, "diagnostics", None), "sync_score", 0.0)
                    )
                    self._alignment_best_sync_score = max(
                        self._alignment_best_sync_score,
                        sync_score,
                    )
                    self._last_success_at = monotonic()
                    self._acquisition_time_ms = acquisition_s * 1000.0
                    self._processing_time_ms = processing_s * 1000.0
                    if acquisition_s + processing_s > self.cpi_period_s:
                        self._overruns += 1
                consecutive_processing_errors = 0
                ever_synchronized = True
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
                target=self._cleanup_source,
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

    def _cleanup_source(self) -> None:
        try:
            self._close_source_once()
        finally:
            with self._source_condition:
                if self._cleanup_worker is threading.current_thread():
                    self._cleanup_worker = None
                self._source_condition.notify_all()

    def _close_source_once(self) -> None:
        with self._source_condition:
            while self._close_started and self._source_state != self._CLOSED:
                self._source_condition.wait()
            if self._source_state == self._CLOSED:
                return
            if self._open_in_progress:
                self._source_state = self._CLOSE_REQUESTED
                return
            self._close_started = True
            self._source_state = self._CLOSE_REQUESTED
        final_error: Optional[Exception] = None
        for _attempt in range(2):
            try:
                self.source.close()
            except Exception as error:
                final_error = error
            else:
                final_error = None
                break
        with self._source_condition:
            self._close_started = False
            if final_error is None:
                self._source_state = self._CLOSED
            self._source_condition.notify_all()
        if final_error is not None:
            self._record_primary_error(str(final_error))

    def _record_primary_error(self, error: str) -> None:
        if not error:
            return
        with self._state_lock:
            if self._error is None:
                self._error = error
