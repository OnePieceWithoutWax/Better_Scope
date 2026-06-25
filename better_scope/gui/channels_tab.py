"""Channels tab: dynamically built per-channel configuration panel.

Rows are created on connect from the model's ``analog_channels_count`` and let
the user edit each channel's name, vertical scale, offset, position, and label
placement. Writes are verified by read-back in the backend; mismatches are
reported in the status line and the fields are refreshed to the confirmed
values.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

# (field, column label, is_float)
_COLUMNS: list[tuple[str, str, bool]] = [
    ("name", "Name / Label", False),
    ("scale", "Scale (V/div)", True),
    ("offset", "Offset (V)", True),
    ("position", "Position (div)", True),
    ("label_x", "Label X", True),
    ("label_y", "Label Y", True),
]


class ChannelsTab:
    """Per-channel name/scale/offset/position/label-placement editor."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.rows_tag = "channels_rows"
        self.status_tag = "channels_status"
        self.apply_btn_tag = "channels_apply_btn"
        self.refresh_btn_tag = "channels_refresh_btn"
        # index -> {field -> input widget tag}
        self._field_tags: dict[int, dict[str, str]] = {}
        # index -> {field -> last loaded value} (to send only changed fields)
        self._loaded: dict[int, dict[str, Any]] = {}

    def build(self, parent: int | str) -> None:
        """Create the static scaffolding; rows are filled in on connect."""
        with dpg.group(parent=parent):
            dpg.add_text(
                "Channel settings are read from and written to the scope "
                "(writes are verified by read-back)."
            )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Apply Changes",
                    tag=self.apply_btn_tag,
                    callback=lambda: self._apply(),
                    enabled=False,
                )
                dpg.add_button(
                    label="Refresh",
                    tag=self.refresh_btn_tag,
                    callback=lambda: self._reload(),
                    enabled=False,
                )
            dpg.add_text("Connect to a scope to manage channels.", tag=self.status_tag)
            dpg.add_separator()
            dpg.add_group(tag=self.rows_tag)

    def on_connection_changed(self, connected: bool) -> None:
        """Rebuild (or clear) the channel rows based on connection state."""
        self._clear_rows()
        if not connected:
            dpg.configure_item(self.apply_btn_tag, enabled=False)
            dpg.configure_item(self.refresh_btn_tag, enabled=False)
            dpg.set_value(self.status_tag, "Connect to a scope to manage channels.")
            return
        self._reload()

    # -- Loading ---------------------------------------------------------------

    def _reload(self) -> None:
        dpg.set_value(self.status_tag, "Reading channels...")
        self.app.worker.submit(
            self.app.scope.get_channels, self._populate, self._on_error
        )

    def _clear_rows(self) -> None:
        self._field_tags = {}
        self._loaded = {}
        if dpg.does_item_exist(self.rows_tag):
            dpg.delete_item(self.rows_tag, children_only=True)

    def _populate(self, channels: list[dict[str, Any]]) -> None:
        """Main thread: create one editable row per analog channel."""
        self._clear_rows()
        if not channels:
            dpg.set_value(self.status_tag, "No channels reported by this scope.")
            return

        with dpg.table(
            header_row=True,
            parent=self.rows_tag,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="Channel")
            dpg.add_table_column(label="Enabled")
            for _field, col_label, _is_float in _COLUMNS:
                dpg.add_table_column(label=col_label)

            for ch in channels:
                index = ch["index"]
                self._field_tags[index] = {}
                self._loaded[index] = {}
                with dpg.table_row():
                    dpg.add_text(f"CH{index}")
                    dpg.add_checkbox(default_value=bool(ch.get("enabled")), enabled=False)
                    for field, _col_label, is_float in _COLUMNS:
                        tag = f"chan_{field}_{index}"
                        value = ch.get(field)
                        if is_float:
                            fval = 0.0 if value is None else float(value)
                            dpg.add_input_float(
                                tag=tag, default_value=fval, width=110, step=0,
                                format="%.4g",
                            )
                            self._loaded[index][field] = fval
                        else:
                            sval = "" if value is None else str(value)
                            dpg.add_input_text(
                                tag=tag, default_value=sval, width=160, hint="(unnamed)"
                            )
                            self._loaded[index][field] = sval
                        self._field_tags[index][field] = tag

        dpg.configure_item(self.apply_btn_tag, enabled=True)
        dpg.configure_item(self.refresh_btn_tag, enabled=True)
        dpg.set_value(self.status_tag, f"{len(channels)} channel(s) loaded.")

    # -- Applying --------------------------------------------------------------

    def _changed_props(self, index: int) -> dict[str, Any]:
        """Collect only fields whose widget value differs from what was loaded."""
        props: dict[str, Any] = {}
        for field, _col_label, is_float in _COLUMNS:
            tag = self._field_tags[index][field]
            current = dpg.get_value(tag)
            loaded = self._loaded[index].get(field)
            if is_float:
                current = float(current)
                if loaded is None or not math.isclose(
                    current, float(loaded), rel_tol=1e-9, abs_tol=1e-12
                ):
                    props[field] = current
            elif str(current) != str(loaded):
                props[field] = str(current)
        return props

    def _apply(self) -> None:
        """Write changed channel properties (worker thread)."""
        if not self.app.scope.is_connected():
            dpg.set_value(self.status_tag, "Not connected.")
            return
        pending = {i: self._changed_props(i) for i in self._field_tags}
        pending = {i: p for i, p in pending.items() if p}
        if not pending:
            dpg.set_value(self.status_tag, "No changes to apply.")
            return

        dpg.configure_item(self.apply_btn_tag, enabled=False)
        dpg.set_value(self.status_tag, "Applying changes...")

        def _write() -> dict[int, dict[str, Any]]:
            return {
                index: self.app.scope.set_channel_properties(index, props)
                for index, props in pending.items()
            }

        self.app.worker.submit(_write, self._on_applied, self._on_error)

    def _on_applied(self, results: dict[int, dict[str, Any]]) -> None:
        """Main thread: refresh widgets to confirmed values; report mismatches."""
        mismatch_msgs: list[str] = []
        for index, result in results.items():
            for field, actual in result["applied"].items():
                tag = self._field_tags[index][field]
                is_float = next(f[2] for f in _COLUMNS if f[0] == field)
                if actual is not None:
                    if is_float:
                        dpg.set_value(tag, float(actual))
                        self._loaded[index][field] = float(actual)
                    else:
                        dpg.set_value(tag, str(actual))
                        self._loaded[index][field] = str(actual)
            for field in result["mismatches"]:
                mismatch_msgs.append(f"CH{index}.{field}")

        dpg.configure_item(self.apply_btn_tag, enabled=True)
        if mismatch_msgs:
            dpg.set_value(
                self.status_tag,
                f"Applied with mismatches (see log): {', '.join(mismatch_msgs)}",
            )
        else:
            dpg.set_value(self.status_tag, "Changes applied and verified.")

    def _on_error(self, exc: Exception) -> None:
        dpg.configure_item(self.apply_btn_tag, enabled=True)
        dpg.configure_item(self.refresh_btn_tag, enabled=True)
        dpg.set_value(self.status_tag, f"Error: {exc}")
