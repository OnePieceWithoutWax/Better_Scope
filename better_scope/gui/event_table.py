"""Event Table: a non-modal window listing the register command history."""

from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.decode.events import EventRow
    from better_scope.gui.app import BetterScopeApp

PAGE_SIZE = 500
_ALL = "All"
_COLUMNS = (
    ("Time (s)", 110),
    ("Bus", 90),
    ("Device", 120),
    ("R/W", 36),
    ("Register", 150),
    ("Value", 90),
    ("Fields", 320),
    ("Error", 200),
)


class EventTable:
    """Filterable, paged table of register accesses; a row click centres the plot."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.window_tag = "event_table_window"
        self.table_tag = "event_table"
        self.bus_tag = "event_filter_bus"
        self.device_tag = "event_filter_device"
        self.register_tag = "event_filter_register"
        self.errors_tag = "event_filter_errors"
        self.page_tag = "event_page_label"

        self._rows: list[EventRow] = []
        self._filtered: list[EventRow] = []
        self._bus_ids: dict[str, str] = {}  # combo label -> bus id
        self._page = 0

    def build(self) -> None:
        """Create the (hidden) window."""
        with dpg.window(
            label="Event Table",
            tag=self.window_tag,
            show=False,
            width=1000,
            height=420,
            pos=(60, 120),
        ):
            with dpg.group(horizontal=True):
                dpg.add_combo([_ALL], label="Bus", tag=self.bus_tag, default_value=_ALL, width=140,
                              callback=lambda: self._apply_filters())
                dpg.add_combo([_ALL], label="Device", tag=self.device_tag, default_value=_ALL, width=160,
                              callback=lambda: self._apply_filters())
                dpg.add_input_text(label="Register", tag=self.register_tag, width=140, hint="name contains",
                                   callback=lambda: self._apply_filters())
                dpg.add_checkbox(label="Errors only", tag=self.errors_tag, callback=lambda: self._apply_filters())
            with dpg.group(horizontal=True):
                dpg.add_button(label="<", width=24, callback=lambda: self._turn(-1))
                dpg.add_button(label=">", width=24, callback=lambda: self._turn(1))
                dpg.add_text("", tag=self.page_tag)
                dpg.add_button(label="Export events CSV", callback=lambda: self.app.decode_tab.export_events())
            with dpg.table(
                tag=self.table_tag,
                header_row=True,
                resizable=True,
                row_background=True,
                borders_innerV=True,
                scrollY=True,
                scrollX=True,
                freeze_rows=1,
                policy=dpg.mvTable_SizingFixedFit,
                height=-1,
            ):
                for label, width in _COLUMNS:
                    dpg.add_table_column(label=label, init_width_or_weight=width)

    def show(self) -> None:
        """Open (or focus) the window."""
        dpg.show_item(self.window_tag)
        dpg.focus_item(self.window_tag)

    def set_rows(self, rows: list[EventRow]) -> None:
        """Replace the command history and refresh the filter choices."""
        self._rows = rows
        names = {r.bus_id: r.bus for r in rows}
        duplicated = {n for n in names.values() if list(names.values()).count(n) > 1}
        buses = {f"{n} [{i}]" if n in duplicated else n: i for i, n in names.items()}
        self._bus_ids = buses
        devices = sorted({r.device for r in rows})
        self._set_combo(self.bus_tag, list(buses))
        self._set_combo(self.device_tag, devices)
        self._apply_filters()

    @property
    def rows(self) -> list[EventRow]:
        """Every row (unfiltered)."""
        return self._rows

    @property
    def filtered_rows(self) -> list[EventRow]:
        """Rows passing the current filters."""
        return self._filtered

    # -- internals ------------------------------------------------------------

    def _set_combo(self, tag: str, items: list[str]) -> None:
        current = dpg.get_value(tag)
        dpg.configure_item(tag, items=[_ALL, *items])
        if current not in items:
            dpg.set_value(tag, _ALL)

    def _apply_filters(self) -> None:
        from better_scope.decode.events import filter_events

        bus = dpg.get_value(self.bus_tag)
        device = dpg.get_value(self.device_tag)
        self._filtered = filter_events(
            self._rows,
            bus_id=None if bus == _ALL else self._bus_ids.get(bus),
            device=None if device == _ALL else device,
            register_text=dpg.get_value(self.register_tag) or "",
            errors_only=bool(dpg.get_value(self.errors_tag)),
        )
        self._page = 0
        self._fill()

    def _turn(self, step: int) -> None:
        pages = max(1, -(-len(self._filtered) // PAGE_SIZE))
        self._page = min(max(self._page + step, 0), pages - 1)
        self._fill()

    def _fill(self) -> None:
        """Show the current page (only ``PAGE_SIZE`` rows exist as widgets)."""
        dpg.delete_item(self.table_tag, children_only=True, slot=1)
        total = len(self._filtered)
        pages = max(1, -(-total // PAGE_SIZE))
        first = self._page * PAGE_SIZE
        page_rows = self._filtered[first:first + PAGE_SIZE]
        dpg.set_value(
            self.page_tag,
            f"Page {self._page + 1}/{pages}  ({total} of {len(self._rows)} events)",
        )
        error_color = (255, 110, 110)
        for row in page_rows:
            with dpg.table_row(parent=self.table_tag):
                dpg.add_selectable(
                    label=row.cells()[0],
                    span_columns=True,
                    user_data=row,
                    callback=self._on_row_clicked,
                )
                for text in row.cells()[1:-1]:
                    dpg.add_text(text)
                dpg.add_text(row.error, color=error_color)

    def _on_row_clicked(self, sender: int | str, value: bool, row: EventRow) -> None:
        dpg.set_value(sender, False)
        self.app.plot_tab.center_on(row.start, row.end)
