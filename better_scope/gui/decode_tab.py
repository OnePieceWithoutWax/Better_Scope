"""Decode tab: bus configuration, register-map bindings, plugins and decode runs.

The decode backend (numpy, decoder discovery) is imported on the worker
thread after the first frame, so it never slows app startup. Every form here
is generated from decoder metadata (roles, options); nothing is
decoder-specific.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.decode.api import Decoder, Option
    from better_scope.decode.model import BusConfig
    from better_scope.decode.registry import DecoderRegistry
    from better_scope.decode.session import DecodeOutcome, DecodeSession, MapBinding
    from better_scope.gui.app import BetterScopeApp

logger = logging.getLogger(__name__)

# Called on the main thread with the decode time in seconds, or ``None`` when
# nothing was decoded (backend not ready, no enabled bus, or an error).
DecodeDone = Callable[[float | None], None]

_ERROR = (255, 110, 110)
_MUTED = (150, 150, 150)
_NONE = "(none)"
_THRESHOLD_MODES = ["auto", "manual"]
# SpiLayout integer fields editable in the map binding form.
_SPI_INT_FIELDS = (
    ("frame_bits", "Frame bits"),
    ("rw_bit", "R/W bit (-1 = none)"),
    ("read_value", "Read when R/W ="),
    ("addr_lsb", "Address LSB"),
    ("addr_bits", "Address bits"),
    ("data_lsb", "Data LSB"),
    ("data_bits", "Data bits"),
)


class DecodeTab:
    """Configure buses and register maps, run decode, export results."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.status_tag = "decode_status"
        self.warn_header_tag = "decode_warn_header"
        self.warn_group_tag = "decode_warn_group"
        self.bus_list_tag = "decode_bus_list"
        self.editor_tag = "decode_editor"
        self.plugins_tag = "decode_plugins"
        self.map_dialog_tag = "decode_map_dialog"
        self.template_dialog_tag = "decode_template_dialog"
        self.events_dialog_tag = "decode_events_dialog"
        self.frames_dialog_tag = "decode_frames_dialog"

        self.session: DecodeSession | None = None
        self.registry: DecoderRegistry | None = None
        self.outcome: DecodeOutcome | None = None
        self._selected: str | None = None
        self._bus_labels: dict[str, str] = {}  # listbox label -> bus id
        self._map_errors: dict[int, str] = {}
        self._busy = False
        self._pending: dict[str, Any] | None = None
        # Completion callbacks of the running decode and of the pending one.
        self._running_done: list[DecodeDone] = []
        self._pending_done: list[DecodeDone] = []

    # -- build ----------------------------------------------------------------

    def build(self, parent: int | str) -> None:
        """Create the tab's widgets and file dialogs."""
        with dpg.group(parent=parent):
            dpg.add_text("Loading decoders...", tag=self.status_tag)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Decode", callback=lambda: self.app.plot_tab.decode_now())
                dpg.add_button(label="Event Table", callback=lambda: self.app.event_table.show())
                dpg.add_button(label="Export events CSV", callback=lambda: self.export_events())
                dpg.add_button(label="Export frames CSV", callback=lambda: self.export_frames())
            with dpg.collapsing_header(label="Warnings (0)", tag=self.warn_header_tag, default_open=False):
                dpg.add_group(tag=self.warn_group_tag)
            with dpg.group(horizontal=True):
                with dpg.child_window(width=300, height=-1):
                    dpg.add_text("Buses")
                    dpg.add_listbox([], tag=self.bus_list_tag, num_items=10, width=-1,
                                    callback=self._on_bus_selected)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Add", callback=lambda: self._add_bus())
                        dpg.add_button(label="Duplicate", callback=lambda: self._duplicate_bus())
                        dpg.add_button(label="Remove", callback=lambda: self._remove_bus())
                    dpg.add_separator()
                    with dpg.collapsing_header(label="Plugins", default_open=False):
                        dpg.add_group(tag=self.plugins_tag)
                dpg.add_child_window(tag=self.editor_tag, width=-1, height=-1)

        self._file_dialog(self.map_dialog_tag, self._on_map_chosen, (".xlsx", ".xlsm"))
        self._file_dialog(self.template_dialog_tag, self._on_template_chosen, (".xlsx",))
        self._file_dialog(self.events_dialog_tag, self._on_events_path, (".csv",))
        self._file_dialog(self.frames_dialog_tag, self._on_frames_path, (".csv",))

    @staticmethod
    def _file_dialog(tag: str, callback: Any, extensions: tuple[str, ...]) -> None:
        with dpg.file_dialog(show=False, tag=tag, width=640, height=420, callback=callback, modal=True):
            for ext in extensions:
                dpg.add_file_extension(ext)

    def _show_dialog(self, tag: str, default_filename: str = "") -> None:
        cfg = self.app.scope.config
        dpg.configure_item(tag, default_path=cfg.get_save_directory(), default_filename=default_filename)
        dpg.show_item(tag)

    # -- backend loading ------------------------------------------------------

    def start(self) -> None:
        """Import the decode backend and discover decoders on the worker thread."""

        def load() -> tuple[DecoderRegistry, DecodeSession]:
            from better_scope.decode.registry import decoder_registry
            from better_scope.decode.session import DecodeSession

            cfg = self.app.scope.config
            registry = decoder_registry()
            try:
                session = DecodeSession.from_dicts(cfg.decode_buses, cfg.decode_maps)
            except Exception:
                logger.exception("Stored decode buses are invalid; starting with none")
                session = DecodeSession()
            return registry, session

        self.app.worker.submit(load, self._on_backend_ready, self._on_backend_error)

    def _on_backend_ready(self, loaded: tuple[DecoderRegistry, DecodeSession]) -> None:
        self.registry, self.session = loaded
        if self.session.buses:
            self._selected = self.session.buses[0].bus_id
        self._refresh_bus_list()
        self._draw_plugins()
        self._draw_editor()
        self._check_maps()
        self._set_status(f"{len(self.registry)} decoder(s) available, {len(self.session.buses)} bus(es) configured.")

    def _on_backend_error(self, exc: Exception) -> None:
        self._set_status(f"Decoder backend failed to load: {exc}", error=True)

    @property
    def ready(self) -> bool:
        """Whether the decode backend has loaded."""
        return self.session is not None and self.registry is not None

    # -- public API used by the other tabs --------------------------------------

    def required_sources(self) -> list[str]:
        """Sources the enabled buses need (empty until the backend loads)."""
        return self.session.required_sources() if self.session is not None else []

    def has_enabled_buses(self) -> bool:
        """Whether any bus would decode."""
        return self.session is not None and any(b.enabled for b in self.session.buses)

    def on_connection_changed(self, connected: bool) -> None:
        """Refresh the source dropdowns for the new channel list."""
        if self.ready:
            self._draw_editor()

    def on_sources_changed(self) -> None:
        """Refresh the source dropdowns after a file was loaded."""
        if self.ready:
            self._draw_editor()

    def decode(self, waveforms: dict[str, Any], on_done: DecodeDone | None = None) -> None:
        """Decode ``waveforms`` with the enabled buses on the worker thread.

        A request made while a decode is running is kept (latest wins) and
        runs when the current one finishes.

        Args:
            waveforms: Source name -> waveform.
            on_done: Called when the decode that covers this request ends,
                with its duration in seconds (``None`` if nothing decoded).
        """
        if not self.ready:
            if on_done is not None:
                on_done(None)
            return
        if not self.has_enabled_buses():
            if self.outcome is not None:
                from better_scope.decode.model import DecodeResult

                self.outcome = None
                self.app.event_table.set_rows([])
                self.app.plot_tab.set_decode(DecodeResult(), self.session)
            if on_done is not None:
                on_done(None)
            return
        if self._busy:
            self._pending = waveforms
            if on_done is not None:
                self._pending_done.append(on_done)
            return
        self._busy = True
        self._running_done = [on_done] if on_done is not None else []
        snapshot = self.session.snapshot()
        registry = self.registry

        def work() -> tuple[DecodeOutcome, float]:
            start = time.perf_counter()
            outcome = snapshot.decode(waveforms, registry)
            return outcome, time.perf_counter() - start

        self._set_status("Decoding...")
        self.app.worker.submit(
            work,
            lambda done: self._on_decoded(done[0], snapshot, done[1]),
            self._on_decode_error,
        )

    def _finish(self, seconds: float | None) -> None:
        """Report the finished decode and start the pending one, if any."""
        self._busy = False
        callbacks, self._running_done = self._running_done, []
        for callback in callbacks:
            callback(seconds)
        if self._pending is not None:
            waveforms, self._pending = self._pending, None
            pending_done, self._pending_done = self._pending_done, []
            self.decode(waveforms)
            if self._busy:
                self._running_done.extend(pending_done)
            else:
                for callback in pending_done:
                    callback(None)

    def apply_embedded(self, bus_dicts: list[dict], map_dicts: list[dict], mapping: dict[str, str]) -> None:
        """Replace the buses and map bindings with those stored in a waveform file.

        Args:
            bus_dicts: Bus configs from the file.
            map_dicts: Map bindings from the file.
            mapping: Source renames (file source -> loaded source name).
        """
        from better_scope.decode.session import DecodeSession

        session = DecodeSession.from_dicts(bus_dicts, map_dicts)
        session.remap_sources(mapping)
        self.session = session
        self._selected = session.buses[0].bus_id if session.buses else None
        self._persist()
        self._refresh_bus_list()
        self._draw_editor()
        self._check_maps()

    # -- decode results -------------------------------------------------------

    def _on_decoded(self, outcome: DecodeOutcome, snapshot: DecodeSession, seconds: float) -> None:
        from better_scope.decode.events import event_rows

        self.outcome = outcome
        errors_changed = False
        if snapshot.maps == (self.session.maps if self.session else None) and outcome.map_errors != self._map_errors:
            self._map_errors = dict(outcome.map_errors)
            errors_changed = True
        result = outcome.result
        n_frames = sum(len(b) for levels in result.frames.values() for b in levels.values())
        n_errors = len(result.errors)
        self.app.event_table.set_rows(event_rows(outcome.accesses, snapshot.bus_names()))
        self.app.plot_tab.set_decode(result, snapshot)

        summary = (
            f"Decoded {sum(b.enabled for b in snapshot.buses)} bus(es): {n_frames} frames, "
            f"{len(outcome.accesses)} register accesses, {n_errors} error frame(s)."
        )
        if result.warnings:
            summary += f" {len(result.warnings)} warning(s): {result.warnings[0]}"
        self._set_status(summary, error=bool(result.warnings))
        self.app.plot_tab.set_status(summary)
        self._show_warnings(result.warnings)
        if errors_changed:
            self._draw_editor()
        self._finish(seconds)

    def _on_decode_error(self, exc: Exception) -> None:
        self._set_status(f"Decode failed: {exc}", error=True)
        self.app.plot_tab.set_status(f"Decode failed: {exc}")
        self._finish(None)

    def _show_warnings(self, warnings: list[str]) -> None:
        dpg.configure_item(self.warn_header_tag, label=f"Warnings ({len(warnings)})")
        dpg.delete_item(self.warn_group_tag, children_only=True)
        for w in warnings[:200]:
            dpg.add_text(w, parent=self.warn_group_tag, color=_ERROR, wrap=900)
        if len(warnings) > 200:
            dpg.add_text(f"... {len(warnings) - 200} more", parent=self.warn_group_tag)

    def _set_status(self, text: str, error: bool = False) -> None:
        dpg.set_value(self.status_tag, text)
        dpg.configure_item(self.status_tag, color=_ERROR if error else (255, 255, 255))

    # -- export ---------------------------------------------------------------

    def export_events(self) -> None:
        """Ask for a path and write the (filtered) command history to CSV."""
        if not self.app.event_table.rows:
            self._set_status("No register accesses to export; decode a bus with a register map or PMBus first.")
            return
        self._show_dialog(self.events_dialog_tag, "events")

    def export_frames(self) -> None:
        """Ask for a path and write the frames of the shown levels/buses to CSV."""
        if self.outcome is None:
            self._set_status("Nothing decoded yet.")
            return
        self._show_dialog(self.frames_dialog_tag, "frames")

    def _on_events_path(self, sender: int | str, app_data: dict) -> None:
        from better_scope.decode.export import events_to_csv

        path = Path(app_data["file_path_name"])
        rows = self.app.event_table.filtered_rows
        self.app.worker.submit(
            lambda: events_to_csv(rows, path),
            lambda p: self._set_status(f"Exported {len(rows)} event(s) to {p}"),
            lambda e: self._set_status(f"Export failed: {e}", error=True),
        )

    def _on_frames_path(self, sender: int | str, app_data: dict) -> None:
        from better_scope.decode.export import frames_to_csv

        if self.outcome is None:
            return
        path = Path(app_data["file_path_name"])
        result = self.outcome.result
        levels = self.app.plot_tab.shown_levels()
        buses = self.app.plot_tab.visible_bus_ids()
        self.app.worker.submit(
            lambda: frames_to_csv(result, path, levels, buses),
            lambda p: self._set_status(f"Exported frames (levels {levels}) to {p}"),
            lambda e: self._set_status(f"Export failed: {e}", error=True),
        )

    # -- bus list -------------------------------------------------------------

    def _persist(self) -> None:
        if self.session is None:
            return
        cfg = self.app.scope.config
        cfg.decode_buses = self.session.bus_dicts()
        cfg.decode_maps = self.session.map_dicts()

    def _refresh_bus_list(self) -> None:
        self._bus_labels = {}
        for i, bus in enumerate(self.session.buses if self.session else [], start=1):
            label = f"{i}. {bus.name}" + ("" if bus.enabled else "  [off]")
            self._bus_labels[label] = bus.bus_id
        labels = list(self._bus_labels)
        dpg.configure_item(self.bus_list_tag, items=labels)
        current = next((lb for lb, bid in self._bus_labels.items() if bid == self._selected), None)
        if current is not None:
            dpg.set_value(self.bus_list_tag, current)

    def _selected_bus(self) -> BusConfig | None:
        if self.session is None or self._selected is None:
            return None
        return self.session.bus(self._selected)

    def _on_bus_selected(self, sender: int | str, label: str) -> None:
        self._selected = self._bus_labels.get(label)
        self._draw_editor()

    def _add_bus(self) -> None:
        if not self.ready:
            return
        from better_scope.decode.model import BusConfig
        from better_scope.decode.session import new_bus_id

        decoder_id = "uart" if "uart" in self.registry else self.registry.ids()[0]
        bus = BusConfig(new_bus_id(), f"Bus {len(self.session.buses) + 1}", decoder_id)
        self.session.buses.append(bus)
        self._selected = bus.bus_id
        self._changed(redraw=True)

    def _duplicate_bus(self) -> None:
        bus = self._selected_bus()
        if bus is None:
            return
        self._selected = self.session.duplicate_bus(bus.bus_id).bus_id
        self._changed(redraw=True)

    def _remove_bus(self) -> None:
        bus = self._selected_bus()
        if bus is None:
            return
        index = self.session.buses.index(bus)
        self.session.remove_bus(bus.bus_id)
        remaining = self.session.buses
        self._selected = remaining[min(index, len(remaining) - 1)].bus_id if remaining else None
        self._map_errors = {}
        self._changed(redraw=True)
        self._check_maps()

    def _changed(self, redraw: bool = False) -> None:
        """Persist and refresh after an edit."""
        self._persist()
        self._refresh_bus_list()
        if redraw:
            self._draw_editor()

    # -- plugins --------------------------------------------------------------

    def _draw_plugins(self) -> None:
        dpg.delete_item(self.plugins_tag, children_only=True)
        if self.registry is None:
            return
        for plugin_id, cls in self.registry.items():
            dpg.add_text(f"{cls.name} ({plugin_id}) v{cls.version}", parent=self.plugins_tag)
            dpg.add_text(f"  {self.registry.origins.get(plugin_id, '?')}", parent=self.plugins_tag,
                         color=_MUTED, wrap=280)
        for message in self.registry.errors:
            dpg.add_text(message, parent=self.plugins_tag, color=_ERROR, wrap=280)
        dpg.add_button(label="Rescan plugins", parent=self.plugins_tag, callback=lambda: self._rescan())

    def _rescan(self) -> None:
        from better_scope.decode.registry import decoder_registry

        self.registry = decoder_registry(refresh=True)
        self._draw_plugins()
        self._draw_editor()

    # -- bus editor -----------------------------------------------------------

    def _sources_for(self, bus: BusConfig) -> list[str]:
        sources = list(self.app.plot_tab.available_sources())
        sources += [s for s in bus.role_map.values() if s and s not in sources]
        return sources

    def _chain(self, bus: BusConfig) -> list[type[Decoder]] | None:
        try:
            return self.registry.chain(bus.decoder_id)
        except KeyError:
            return None

    def _draw_editor(self) -> None:
        dpg.delete_item(self.editor_tag, children_only=True)
        bus = self._selected_bus()
        if bus is None:
            dpg.add_text("Add a bus to start.", parent=self.editor_tag)
            return
        with dpg.group(parent=self.editor_tag):
            with dpg.group(horizontal=True):
                dpg.add_input_text(label="Name", default_value=bus.name, width=220,
                                   callback=lambda s, v: self._set_name(bus, v))
                dpg.add_checkbox(label="Enabled", default_value=bus.enabled,
                                 callback=lambda s, v: self._set_enabled(bus, v))
            self._decoder_combo(bus)
            chain = self._chain(bus)
            if chain is None:
                dpg.add_text(f"Decoder {bus.decoder_id!r} is not installed.", color=_ERROR)
                return
            stack = " -> ".join(cls.id for cls in chain)
            dpg.add_text(f"Stack: {stack}", color=_MUTED)
            if chain[-1].description:
                dpg.add_text(chain[-1].description, color=_MUTED, wrap=700)
            dpg.add_separator()
            self._roles_form(bus, chain[0])
            dpg.add_separator()
            self._options_form(bus, chain)
            dpg.add_separator()
            self._maps_form(bus, chain)

    def _decoder_combo(self, bus: BusConfig) -> None:
        labels = {f"{cls.name} ({plugin_id})": plugin_id for plugin_id, cls in self.registry.items()}
        current = next((lb for lb, pid in labels.items() if pid == bus.decoder_id), f"? ({bus.decoder_id})")
        dpg.add_combo(list(labels), label="Decoder", default_value=current, width=260,
                      callback=lambda s, v: self._set_decoder(bus, labels[v]))

    def _roles_form(self, bus: BusConfig, root: type[Decoder]) -> None:
        dpg.add_text("Signals")
        if root.require_one_of:
            dpg.add_text(f"Map at least one of: {', '.join(root.require_one_of)}", color=_MUTED)
        sources = [_NONE, *self._sources_for(bus)]
        with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit, borders_innerH=True):
            for label in ("Role", "Source", "Threshold", "Level (V)", "Hysteresis (V, 0 = auto)"):
                dpg.add_table_column(label=label)
            for role in root.roles:
                source = bus.role_map.get(role.id, "")
                th = bus.threshold_for(source) if source else None
                with dpg.table_row():
                    dpg.add_text(role.label + ("" if role.required else " (optional)"))
                    if role.help:
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(role.help)
                    dpg.add_combo(sources, default_value=source or _NONE, width=130,
                                  callback=lambda s, v, r=role.id: self._set_source(bus, r, v))
                    dpg.add_combo(_THRESHOLD_MODES, default_value=th.mode if th else "auto", width=90,
                                  enabled=bool(source),
                                  callback=lambda s, v, src=source: self._set_threshold(bus, src, mode=v))
                    dpg.add_input_double(default_value=th.level if th else 0.0, width=110, format="%.4g",
                                         enabled=bool(source),
                                         callback=lambda s, v, src=source: self._set_threshold(bus, src, level=v))
                    dpg.add_input_double(default_value=(th.hysteresis or 0.0) if th else 0.0, width=110,
                                         format="%.4g", min_value=0.0, min_clamped=True, enabled=bool(source),
                                         callback=lambda s, v, src=source: self._set_threshold(bus, src, hysteresis=v))

    def _options_form(self, bus: BusConfig, chain: list[type[Decoder]]) -> None:
        dpg.add_text("Options")
        seen: set[str] = set()
        for cls in chain:
            opts = [o for o in cls.options if o.id not in seen]
            if not opts:
                continue
            dpg.add_text(cls.name, color=_MUTED)
            for opt in opts:
                seen.add(opt.id)
                self._option_widget(bus, opt)

    def _option_widget(self, bus: BusConfig, opt: Option) -> None:
        value = bus.options.get(opt.id, opt.default)
        try:
            value = opt.coerce(value)
        except ValueError:
            value = opt.default
        label = f"{opt.label}##{opt.id}"
        if opt.choices:
            texts = [str(c) for c in opt.choices]
            dpg.add_combo(texts, label=label, default_value=str(value), width=160,
                          callback=lambda s, v, o=opt, t=texts: self._set_option(bus, o, o.choices[t.index(v)]))
        elif opt.type is bool:
            dpg.add_checkbox(label=label, default_value=bool(value),
                             callback=lambda s, v, o=opt: self._set_option(bus, o, bool(v)))
        elif opt.type is int:
            dpg.add_input_int(label=label, default_value=int(value), width=160,
                              callback=lambda s, v, o=opt: self._set_option(bus, o, int(v)))
        elif opt.type is float:
            dpg.add_input_double(label=label, default_value=float(value), width=160, format="%.6g",
                                 callback=lambda s, v, o=opt: self._set_option(bus, o, float(v)))
        else:
            dpg.add_input_text(label=label, default_value=str(value), width=160,
                               callback=lambda s, v, o=opt: self._set_option(bus, o, str(v)))
        if opt.help:
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text(opt.help, wrap=400)

    def _maps_form(self, bus: BusConfig, chain: list[type[Decoder]]) -> None:
        ids = {cls.id for cls in chain}
        is_smbus = "smbus" in ids
        is_spi = chain[0].id == "spi"
        dpg.add_text("Register maps")
        if not (is_smbus or is_spi):
            dpg.add_text("Register maps apply to SMBus/PMBus and SPI buses.", color=_MUTED)
            return
        if "pmbus" in ids and not any(m.bus_id == bus.bus_id for m in self.session.maps):
            dpg.add_text("No map bound: PMBus standard command names are used.", color=_MUTED)
        for index, entry in enumerate(self.session.maps):
            if entry.bus_id != bus.bus_id:
                continue
            with dpg.group(horizontal=True):
                dpg.add_button(label="Remove", callback=lambda s, a, i=index: self._remove_map(i))
                dpg.add_text(Path(entry.path).name if entry.path else "PMBus standard command table")
                if entry.path:
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(entry.path)
            with dpg.group(indent=20):
                if is_smbus:
                    addr = "" if entry.address is None else f"0x{entry.address:02X}"
                    dpg.add_input_text(label="SMBus address (7-bit, blank = from map)", default_value=addr,
                                       width=90, on_enter=True,
                                       callback=lambda s, v, e=entry: self._set_map_address(e, v))
                if is_spi:
                    self._spi_layout_form(entry)
                if index in self._map_errors:
                    dpg.add_text(self._map_errors[index], color=_ERROR, wrap=700)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Add Excel map...", callback=lambda: self._show_dialog(self.map_dialog_tag))
            if "pmbus" in ids:
                dpg.add_button(label="Add PMBus standard table", callback=lambda: self._add_map(bus, ""))
            dpg.add_button(label="Save map template...",
                           callback=lambda: self._show_dialog(self.template_dialog_tag, "register_map_template"))

    def _spi_layout_form(self, entry: MapBinding) -> None:
        from better_scope.decode.regmap.mapper import SpiLayout

        defaults = SpiLayout()
        for field_id, label in _SPI_INT_FIELDS:
            value = entry.spi_layout.get(field_id, getattr(defaults, field_id))
            dpg.add_input_int(label=label, default_value=-1 if value is None else int(value), width=110,
                              callback=lambda s, v, f=field_id: self._set_layout(entry, f, v))
        for field_id, label, choices in (
            ("read_line", "Read data on", ["miso", "mosi"]),
            ("default_direction", "Direction without R/W bit", ["write", "read"]),
        ):
            dpg.add_combo(choices, label=label, width=110,
                          default_value=entry.spi_layout.get(field_id, getattr(defaults, field_id)),
                          callback=lambda s, v, f=field_id: self._set_layout(entry, f, v))

    # -- editor callbacks -----------------------------------------------------

    def _set_name(self, bus: BusConfig, name: str) -> None:
        bus.name = name or bus.bus_id
        self._changed()

    def _set_enabled(self, bus: BusConfig, enabled: bool) -> None:
        bus.enabled = bool(enabled)
        self._changed()

    def _set_decoder(self, bus: BusConfig, decoder_id: str) -> None:
        bus.decoder_id = decoder_id
        chain = self._chain(bus) or []
        role_ids = {r.id for r in chain[0].roles} if chain else set()
        option_ids = {o.id for cls in chain for o in cls.options}
        bus.role_map = {r: s for r, s in bus.role_map.items() if r in role_ids}
        bus.options = {k: v for k, v in bus.options.items() if k in option_ids}
        self._changed(redraw=True)

    def _set_source(self, bus: BusConfig, role_id: str, source: str) -> None:
        if source == _NONE:
            bus.role_map.pop(role_id, None)
        else:
            bus.role_map[role_id] = source
        self._changed(redraw=True)

    def _set_threshold(self, bus: BusConfig, source: str, **changes: Any) -> None:
        from better_scope.decode.model import Threshold

        th = bus.thresholds.setdefault(source, Threshold())
        if "mode" in changes:
            th.mode = changes["mode"]
        if "level" in changes:
            th.level = float(changes["level"])
        if "hysteresis" in changes:
            th.hysteresis = float(changes["hysteresis"]) or None
        self._changed()

    def _set_option(self, bus: BusConfig, opt: Option, value: Any) -> None:
        bus.options[opt.id] = value
        self._changed()

    # -- map bindings ---------------------------------------------------------

    def _on_map_chosen(self, sender: int | str, app_data: dict) -> None:
        bus = self._selected_bus()
        if bus is not None:
            self._add_map(bus, app_data["file_path_name"])

    def _add_map(self, bus: BusConfig, path: str) -> None:
        from better_scope.decode.session import MapBinding

        self.session.maps.append(MapBinding(bus.bus_id, path))
        self._persist()
        self._draw_editor()
        self._check_maps()

    def _remove_map(self, index: int) -> None:
        del self.session.maps[index]
        self._map_errors = {}
        self._persist()
        self._draw_editor()
        self._check_maps()

    def _set_map_address(self, entry: MapBinding, text: str) -> None:
        text = text.strip()
        try:
            address = int(text, 0) if text else None
            if address is not None and not 0 <= address <= 0x7F:
                raise ValueError
        except ValueError:
            self._set_status(f"Invalid SMBus address {text!r}: use 0x00-0x7F.", error=True)
            return
        entry.address = address
        self._persist()

    def _set_layout(self, entry: MapBinding, field_id: str, value: Any) -> None:
        if field_id == "rw_bit" and value < 0:
            value = None
        entry.spi_layout[field_id] = value
        try:
            entry.layout()
        except ValueError as e:
            self._set_status(f"SPI layout: {e}", error=True)
        self._persist()

    def _check_maps(self) -> None:
        """Load every bound map on the worker and show parse errors inline."""
        if self.session is None or not self.session.maps:
            return
        snapshot = self.session.snapshot()

        def done(result: tuple[list, dict[int, str]]) -> None:
            if self.session is not None and snapshot.maps == self.session.maps:
                self._map_errors = result[1]
                self._draw_editor()

        self.app.worker.submit(snapshot.device_bindings, done)

    def _on_template_chosen(self, sender: int | str, app_data: dict) -> None:
        from better_scope.decode.regmap.excel import write_template

        path = Path(app_data["file_path_name"])
        self.app.worker.submit(
            lambda: write_template(path),
            lambda p: self._set_status(f"Saved register map template to {p}"),
            lambda e: self._set_status(f"Could not save template: {e}", error=True),
        )
