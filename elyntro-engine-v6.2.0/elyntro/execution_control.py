"""Cooperative cancellation and bounded waiting for step handlers."""
from __future__ import annotations

import threading
import time


class StepTimeoutError(TimeoutError):
    """The executor stopped waiting for a handler after its configured deadline."""


class ExecutionCancelledError(RuntimeError):
    """Raised when a handler reaches a cancellation checkpoint after cancellation."""


class CancellationToken:
    """Thread-safe cancellation token passed to trusted handlers.

    Cancellation is cooperative: handlers should call ``context.checkpoint()`` at
    safe points. Python cannot safely kill a running thread; timeout stops the
    executor waiting, but does not forcibly terminate a non-cooperative handler.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def checkpoint(self, deadline: float | None = None) -> None:
        if self.is_cancelled:
            raise ExecutionCancelledError("execution cancellation requested")
        if deadline is not None and time.monotonic() >= deadline:
            raise StepTimeoutError("step deadline exceeded")
