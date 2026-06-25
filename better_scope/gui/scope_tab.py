"""Scope tab: scan for instruments, select one, and connect."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp


class ScopeTab:
    """Connection controls and discovered-instrument selection."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self._instrument_map: dict[str, dict[str, Any]] = {}

        self.status_tag = "scope_status"
        self.combo_tag = "scope_combo"
        self.device_tag = "scope_device_info"
        self.scan_btn_tag = "scope_scan_btn"
        self.connect_btn_tag = "scope_connect_btn"

    def build(self, parent: int | str) -> None:
        """Create the tab's widgets under ``parent``."""
        with dpg.group(parent=parent):
            dpg.add_text("Status: Not Connected", tag=self.status_tag)
            dpg.add_separator()
            dpg.add_text("Discovered Instruments:")
            dpg.add_combo(items=[], tag=self.combo_tag, width=560)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Scan for Scope",
                    tag=self.scan_btn_tag,
                    callback=lambda: self.start_scan(),
                )
                dpg.add_button(
                    label="Connect Selected",
                    tag=self.connect_btn_tag,
                    callback=lambda: self._connect_selected(),
                )
            dpg.add_separator()
            dpg.add_text("Currently Connected:")
            dpg.add_text(self._device_text(), tag=self.device_tag)

    # -- Scanning & connecting -------------------------------------------------

    def start_scan(self) -> None:
        """Kick off a background scan + auto-connect."""
        dpg.set_value(self.status_tag, "Status: Scanning...")
        dpg.configure_item(self.scan_btn_tag, enabled=False)
        self.app.worker.submit(self._do_scan, self._on_scan_done, self._on_error)

    def _do_scan(self) -> bool:
        """Worker thread: scan, then auto-connect. Returns connection state."""
        self.app.scope.scan_for_instruments()
        return self.app.scope.auto_setup_scope()

    def _on_scan_done(self, connected: bool) -> None:
        """Main thread: refresh the combo and connection-dependent UI."""
        self._refresh_combo()
        dpg.configure_item(self.scan_btn_tag, enabled=True)
        if not connected and not self.app.scope.instrument_list:
            dpg.set_value(self.status_tag, "Status: No instruments found")
        elif not connected:
            dpg.set_value(self.status_tag, "Status: No supported scope found")
        self.app.on_connection_changed()

    def _connect_selected(self) -> None:
        """Connect to the instrument currently selected in the combo."""
        selected = dpg.get_value(self.combo_tag)
        instr = self._instrument_map.get(selected)
        if not instr:
            dpg.set_value(self.status_tag, "Status: Select an instrument first")
            return
        addr = instr["addr"]
        dpg.set_value(self.status_tag, f"Status: Connecting to {addr}...")
        dpg.configure_item(self.connect_btn_tag, enabled=False)
        self.app.worker.submit(
            lambda: self.app.scope.setup_scope(addr, instr_info=instr),
            self._on_connect_done,
            self._on_error,
        )

    def _on_connect_done(self, connected: bool) -> None:
        """Main thread: refresh after an explicit connect attempt."""
        dpg.configure_item(self.connect_btn_tag, enabled=True)
        if not connected:
            dpg.set_value(self.status_tag, "Status: Connection failed")
        self.app.on_connection_changed()

    def _on_error(self, exc: Exception) -> None:
        """Main thread: surface a worker error in the status line."""
        dpg.configure_item(self.scan_btn_tag, enabled=True)
        dpg.configure_item(self.connect_btn_tag, enabled=True)
        dpg.set_value(self.status_tag, f"Status: Error - {exc}")

    # -- UI helpers ------------------------------------------------------------

    def _refresh_combo(self) -> None:
        """Rebuild the combo from the backend's discovered-instrument list."""
        self._instrument_map = {}
        items: list[str] = []
        for instr in self.app.scope.instrument_list:
            model = instr.get("model_num") or "Unknown"
            addr = instr.get("addr", "Unknown")
            label = f"{model} @ {addr}"
            items.append(label)
            self._instrument_map[label] = instr
        dpg.configure_item(self.combo_tag, items=items)

    def _device_text(self) -> str:
        info = self.app.scope.get_device_info()
        return info if info else "No device detected"

    def on_connection_changed(self, connected: bool) -> None:
        """Update status and device labels when the connection changes."""
        if connected:
            dpg.set_value(self.status_tag, "Status: Connected")
        elif dpg.get_value(self.status_tag).startswith("Status: Connecting"):
            dpg.set_value(self.status_tag, "Status: Not Connected")
        dpg.set_value(self.device_tag, self._device_text())
