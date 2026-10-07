"""Run blocking HTTP calls off the GUI thread and deliver results back on it.

Uses daemon threads rather than QThreadPool: the pool blocks application exit
until running jobs finish, and a high-reasoning /chat call can take minutes.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

_MAX_PARALLEL = threading.BoundedSemaphore(8)
_in_flight: set["_Call"] = set()  # keeps each _Call alive until it has delivered


class _Call(QObject):
    finished = Signal(object, object)  # (result, error)

    def __init__(self, on_done: Callable[[Any], None], on_error: Callable[[Exception], None]):
        super().__init__()  # created on the GUI thread, so _deliver runs there (queued connection)
        self._on_done, self._on_error = on_done, on_error
        self.finished.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, result, error) -> None:
        _in_flight.discard(self)
        if error is None:
            self._on_done(result)
        else:
            self._on_error(error)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None],
              on_error: Callable[[Exception], None]) -> None:
    call = _Call(on_done, on_error)
    _in_flight.add(call)

    def worker() -> None:
        with _MAX_PARALLEL:
            try:
                result, error = fn(), None
            except Exception as exc:  # delivered to on_error on the GUI thread
                result, error = None, exc
        call.finished.emit(result, error)

    threading.Thread(target=worker, daemon=True, name="honcho-call").start()


class Latest:
    """Detects superseded requests: if you click peer A then peer B, A's late reply is ignored."""

    def __init__(self) -> None:
        self._serial: dict[str, int] = {}

    def ticket(self, key: str = "") -> Callable[[], bool]:
        n = self._serial[key] = self._serial.get(key, 0) + 1
        return lambda: self._serial.get(key) == n
