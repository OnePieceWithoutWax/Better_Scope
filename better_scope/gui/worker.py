"""Background work + main-thread UI-update marshalling for DearPyGui.

VISA operations (scan, connect, capture, acquire) are slow and must not block
the render loop. They run on a worker thread; any resulting UI mutation,
especially dynamic widget creation, is queued and applied on the main thread by
:meth:`Worker.drain`, which the render loop calls once per frame. DearPyGui
item creation is not thread-safe, so it must always go through the queue.
"""

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class Worker:
    """Runs callables off the main thread and marshals results back to it."""

    def __init__(self) -> None:
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

    def submit(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Run ``fn`` on a background thread.

        Args:
            fn: Work to run off the main thread (e.g. a VISA call).
            on_done: Called on the main thread with ``fn``'s result on success.
            on_error: Called on the main thread with the exception on failure.
        """

        def _run() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - reported via on_error
                logger.exception("Background task failed")
                if on_error is not None:
                    self.post(lambda: on_error(exc))
                return
            if on_done is not None:
                self.post(lambda: on_done(result))

        threading.Thread(target=_run, daemon=True).start()

    def post(self, fn: Callable[[], None]) -> None:
        """Queue ``fn`` to run on the main thread on the next :meth:`drain`."""
        self._ui_queue.put(fn)

    def drain(self) -> None:
        """Run all queued main-thread callables. Call once per frame."""
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                logger.exception("UI update callback failed")
