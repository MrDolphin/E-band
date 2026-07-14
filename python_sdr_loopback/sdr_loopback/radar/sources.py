"""Interchangeable finite-CPI data sources for the FMCW processor."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .config import RadarConfig
from .models import RadarCapture, SyntheticTarget
from .simulator import simulate_capture
from .storage import load_capture


class RadarDataSource(Protocol):
    def open(self) -> None: ...

    def capture(self) -> RadarCapture: ...

    def close(self) -> None: ...


class SyntheticTargetSource:
    """Generate one deterministic synthetic CPI per capture call."""

    def __init__(
        self,
        config: RadarConfig,
        targets: Sequence[SyntheticTarget] = (),
        *,
        seed: int = 0,
    ) -> None:
        self.config = config
        self.targets = tuple(targets)
        self.seed = seed
        self.frame_index = 0
        self._is_open = False

    def open(self) -> None:
        self.frame_index = 0
        self._is_open = True

    def capture(self) -> RadarCapture:
        if not self._is_open:
            raise RuntimeError("radar data source is not open")
        frame_index = self.frame_index
        capture = simulate_capture(
            self.config,
            self.targets,
            timestamp=float(frame_index),
            seed=self.seed + frame_index,
        )
        self.frame_index += 1
        return capture

    def close(self) -> None:
        self._is_open = False


class IqReplaySource:
    """Replay capture archives in order, optionally looping at the end."""

    def __init__(self, paths: Sequence[str | Path], *, loop: bool = False) -> None:
        self.paths = tuple(Path(path) for path in paths)
        self.loop = loop
        self._index = 0
        self._is_open = False

    def open(self) -> None:
        self._index = 0
        self._is_open = True

    def capture(self) -> RadarCapture:
        if not self._is_open:
            raise RuntimeError("radar data source is not open")
        if not self.paths:
            raise StopIteration("no replay captures configured")
        if self._index >= len(self.paths):
            if not self.loop:
                raise StopIteration("replay captures exhausted")
            self._index = 0
        path = self.paths[self._index]
        capture = load_capture(path)
        self._index += 1
        return capture

    def close(self) -> None:
        self._is_open = False
