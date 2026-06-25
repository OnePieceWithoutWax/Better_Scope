"""Trigger tab: configure the A-trigger and view live trigger status."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

_MODES = ["AUTO", "NORMAL"]
_TYPES = ["EDGE", "PULSE", "RUNT", "LOGIC", "SETHOLD", "TRANSITION", "BUS"]
_SLOPES = ["RISE", "FALL", "EITHER"]
_COUPLINGS = ["DC", "AC", "HFREJ", "LFREJ", "NOISEREJ"]

# Combo field -> (tag suffix, items). ``source`` items are model-dependent.
_COMBO_FIELDS: dict[str, list[str]] = {
    "mode": _MODES,
    "type": _TYPES,
    "slope": _SLOPES,
    "coupling": _COUPLINGS,
}


class TriggerTab:
    """Edit A-trigger mode/type/source/slope/coupling/level; force and 50%."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.body_tag = "trigger_body"
        self.status_tag = "trigger_status"
        self.state_tag = "trigger_state_text"
        self.tags = {
            "mode": "trigger_mode",
            "type": "trigger_type",
            "source": "trigger_source",
            "slope": "trigger_slope",
            "coupling": "trigger_coupling",
            "level": "trigger_level",
        }

    def build(self, parent: int | str) -> None:
        """Create the scaffolding; controls are built on connect."""
        with dpg.group(parent=parent):
            dpg.add_text("Connect to a scope to configure the trigger.", tag=self.status_tag)
            dpg.add_separator()
            dpg.add_group(tag=self.body_tag)

    def on_connection_changed(self, connected: bool) -> None:
        """Build (or clear) the trigger controls based on connection state."""
        dpg.delete_item(self.body_tag, children_only=True)
        if not connected:
            dpg.set_value(self.status_tag, "Connect to a scope to configure the trigger.")
            return

        sources = [f"CH{i}" for i in range(1, self.app.scope.analog_channel_count + 1)]
        sources += ["AUX", "LINE"]

        with dpg.group(parent=self.body_tag):
            dpg.add_combo(_MODES, label="Mode", tag=self.tags["mode"], width=160)
            dpg.add_combo(_TYPES, label="Type", tag=self.tags["type"], width=160)
            dpg.add_combo(sources, label="Edge source", tag=self.tags["source"], width=160)
            dpg.add_combo(_SLOPES, label="Edge slope", tag=self.tags["slope"], width=160)
            dpg.add_combo(
                _COUPLINGS, label="Edge coupling", tag=self.tags["coupling"], width=160
            )
            dpg.add_input_float(
                label="Level (V)", tag=self.tags["level"], width=160, step=0, format="%.4g"
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Apply", callback=lambda: self._apply())
                dpg.add_button(label="Refresh", callback=lambda: self._reload())
                dpg.add_button(label="Force Trigger", callback=lambda: self._force())
                dpg.add_button(label="Set 50% Level", callback=lambda: self._set_50())
            dpg.add_separator()
            dpg.add_text("State: --   Frequency: --", tag=self.state_tag)

        self._reload()

    # -- Load / refresh --------------------------------------------------------

    def _reload(self) -> None:
        dpg.set_value(self.status_tag, "Reading trigger...")
        self.app.worker.submit(self.app.scope.get_trigger, self._populate, self._on_error)

    def _populate(self, settings: dict[str, Any]) -> None:
        for field in ("mode", "type", "source", "slope", "coupling"):
            value = settings.get(field)
            if value is not None:
                dpg.set_value(self.tags[field], str(value).strip().upper())
        level = settings.get("level")
        if level is not None:
            dpg.set_value(self.tags["level"], float(level))
        self._update_state(settings)
        dpg.set_value(self.status_tag, "Trigger settings loaded.")

    def _update_state(self, settings: dict[str, Any]) -> None:
        state = settings.get("state")
        freq = settings.get("frequency")
        freq_text = "--" if freq is None else f"{float(freq):.4g} Hz"
        dpg.set_value(self.state_tag, f"State: {state if state is not None else '--'}"
                      f"   Frequency: {freq_text}")

    # -- Apply / actions -------------------------------------------------------

    def _apply(self) -> None:
        if not self.app.scope.is_connected():
            return
        settings = {
            field: dpg.get_value(self.tags[field])
            for field in ("mode", "type", "source", "slope", "coupling")
            if dpg.get_value(self.tags[field])
        }
        settings["level"] = float(dpg.get_value(self.tags["level"]))
        dpg.set_value(self.status_tag, "Applying trigger settings...")
        self.app.worker.submit(
            lambda: self.app.scope.apply_trigger(settings), self._on_applied, self._on_error
        )

    def _on_applied(self, result: dict[str, Any]) -> None:
        mismatches = result.get("mismatches", [])
        if mismatches:
            dpg.set_value(
                self.status_tag,
                f"Applied with mismatches (see log): {', '.join(mismatches)}",
            )
        else:
            dpg.set_value(self.status_tag, "Trigger settings applied and verified.")
        self._reload()

    def _force(self) -> None:
        self.app.worker.submit(
            self.app.scope.force_trigger,
            lambda _r: dpg.set_value(self.status_tag, "Trigger forced."),
            self._on_error,
        )

    def _set_50(self) -> None:
        self.app.worker.submit(
            self.app.scope.set_trigger_level_50,
            lambda _r: self._reload(),
            self._on_error,
        )

    def _on_error(self, exc: Exception) -> None:
        dpg.set_value(self.status_tag, f"Error: {exc}")
