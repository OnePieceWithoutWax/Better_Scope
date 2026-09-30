"""Command-history rows for the event table and the events CSV.

Turns :class:`~better_scope.decode.regmap.RegisterAccess` records into flat,
display-ready rows and filters them. GUI-agnostic.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from better_scope.decode.regmap.mapper import RegisterAccess

EVENT_COLUMNS: tuple[str, ...] = ("time", "bus", "device", "rw", "register", "value", "fields", "error")


@dataclass(frozen=True)
class EventRow:
    """One register read or write, formatted for display.

    Attributes:
        start: Start time in seconds.
        end: End time in seconds.
        bus_id: Bus id (for filtering and plot lookup).
        bus: Bus display name.
        device: Device name with its SMBus address, e.g. ``PMIC (0x40)``.
        rw: ``"R"`` or ``"W"``.
        register: Register name, hex address, or ``"?"``.
        value: Value as hex, or empty.
        fields: ``NAME=value; ...``.
        error: Problems joined with ``"; "``, or empty.
    """

    start: float
    end: float
    bus_id: str
    bus: str
    device: str
    rw: str
    register: str
    value: str
    fields: str
    error: str

    def cells(self) -> tuple[str, ...]:
        """Cell texts in :data:`EVENT_COLUMNS` order."""
        return (
            f"{self.start:.9g}",
            self.bus,
            self.device,
            self.rw,
            self.register,
            self.value,
            self.fields,
            self.error,
        )


def event_rows(accesses: Iterable[RegisterAccess], bus_names: Mapping[str, str] | None = None) -> list[EventRow]:
    """Build display rows from register accesses.

    Args:
        accesses: Register accesses (command history), any order.
        bus_names: Bus id -> display name (the id is shown if missing).

    Returns:
        Rows sorted by start time.
    """
    names = bus_names or {}
    rows = []
    for a in accesses:
        device = a.device if a.device_address is None else f"{a.device} (0x{a.device_address:02X})"
        rows.append(
            EventRow(
                start=a.start,
                end=a.end,
                bus_id=a.bus_id,
                bus=names.get(a.bus_id, a.bus_id),
                device=device,
                rw="R" if a.direction == "read" else "W",
                register=a.register_name,
                value=a.value_text,
                fields="; ".join(fv.text for fv in a.fields),
                error="; ".join(a.errors),
            )
        )
    rows.sort(key=lambda r: r.start)
    return rows


def filter_events(
    rows: Iterable[EventRow],
    *,
    bus_id: str | None = None,
    device: str | None = None,
    register_text: str = "",
    errors_only: bool = False,
) -> list[EventRow]:
    """Rows matching every given filter.

    Args:
        rows: Rows to filter.
        bus_id: Keep this bus only (``None`` = all).
        device: Keep this device text only (``None`` = all).
        register_text: Case-insensitive substring of the register name.
        errors_only: Keep rows with an error only.

    Returns:
        The matching rows, in input order.
    """
    needle = register_text.strip().lower()
    return [
        r
        for r in rows
        if (bus_id is None or r.bus_id == bus_id)
        and (device is None or r.device == device)
        and (not needle or needle in r.register.lower())
        and (not errors_only or r.error)
    ]
