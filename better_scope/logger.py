"""Logging for Better_Scope.

Uses standard module-level loggers (``logging.getLogger(__name__)``) throughout
the package. :func:`setup_logger` attaches a single :class:`ListHandler` to the
``better_scope`` package logger (and to ``pymeasure``), so all module and driver
logs are captured for the GUI log viewer via normal log propagation -- no logger
objects are threaded through constructors.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class ListHandler(logging.Handler):
    """Logging handler that stores records in a list and notifies callbacks.

    Supports live GUI updates via registered callbacks and exposes formatted
    log entries for display or saving.
    """

    def __init__(self, level: int = logging.DEBUG):
        super().__init__(level)
        self._records: list[logging.LogRecord] = []
        self._callbacks: list[Callable[[logging.LogRecord], None]] = []

        self.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    @property
    def records(self) -> list[logging.LogRecord]:
        """Copy of the raw log records."""
        return self._records.copy()

    @property
    def entries(self) -> list[str]:
        """Formatted log entries as strings."""
        return [self.format(record) for record in self._records]

    def emit(self, record: logging.LogRecord) -> None:
        """Store a record and notify all registered callbacks."""
        self._records.append(record)
        for callback in self._callbacks:
            try:
                callback(record)
            except Exception:
                # A misbehaving callback must never break logging.
                pass

    def clear(self) -> None:
        """Clear all stored log records."""
        self._records.clear()

    def add_callback(self, callback: Callable[[logging.LogRecord], None]) -> None:
        """Register a callback invoked with each new log record."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[logging.LogRecord], None]) -> None:
        """Remove a previously registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def save(self, filepath: Path, app_version: str = "unknown") -> Path:
        """Save the formatted log entries to a text file.

        Args:
            filepath: Destination path for the log file.
            app_version: Application version recorded in the file header.

        Returns:
            The path the log was written to.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(
                f"Better_Scope Log - Saved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            f.write(f"Application Version: {app_version}\n")
            f.write("=" * 60 + "\n\n")
            for entry in self.entries:
                f.write(entry + "\n")

        return filepath


def setup_logger(level: int = logging.DEBUG) -> ListHandler:
    """Attach a :class:`ListHandler` to the package and driver loggers.

    Args:
        level: Minimum log level captured.

    Returns:
        The installed :class:`ListHandler` (held by the backend for the GUI).
    """
    handler = ListHandler(level)
    for name in ("better_scope", "pymeasure"):
        log = logging.getLogger(name)
        log.setLevel(level)
        # Avoid stacking duplicate ListHandlers on repeated setup calls.
        for existing in [h for h in log.handlers if isinstance(h, ListHandler)]:
            log.removeHandler(existing)
        log.addHandler(handler)

    return handler
