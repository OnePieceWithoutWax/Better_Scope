"""CSV export of decoded frames and of the register command history."""

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from better_scope.decode.events import EVENT_COLUMNS, EventRow
from better_scope.decode.model import DecodeResult

CSV_COLUMNS: tuple[str, ...] = (
    "time_start", "time_end", "bus", "decoder", "level", "kind", "text", "data", "error",
)


def frames_to_csv(
    result: DecodeResult,
    path: Path,
    levels: Iterable[int] | None = None,
    bus_ids: Iterable[str] | None = None,
) -> Path:
    """Write frames to a CSV file, sorted by start time.

    Args:
        result: Decode result to export.
        path: Destination file (parent folders are created).
        levels: Levels to include (all if ``None``).
        bus_ids: Buses to include (all if ``None``).

    Returns:
        The path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for frame in result.iter_frames(levels, bus_ids):
            writer.writerow([
                repr(frame.start),
                repr(frame.end),
                frame.bus_id,
                frame.decoder_id,
                frame.level,
                frame.kind,
                frame.long_text,
                json.dumps(frame.data, default=str),
                frame.error or "",
            ])
    return path


def events_to_csv(rows: Iterable[EventRow], path: Path) -> Path:
    """Write command-history rows (see :mod:`better_scope.decode.events`) to CSV.

    Times are written at full precision; the other columns match the event
    table.

    Args:
        rows: Rows to write, in order.
        path: Destination file (parent folders are created).

    Returns:
        The path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EVENT_COLUMNS)
        for row in rows:
            writer.writerow([repr(row.start), *row.cells()[1:]])
    return path
