"""Plot tab: acquire or load waveforms, plot them, and show decode lanes.

Full-resolution waveforms are kept for decoding; the plot shows a min/max
decimated copy of the visible range, recomputed in :meth:`PlotTab.tick` when
the X range or plot width changes. Decoded buses get one lane each below the
waveforms (``dpg.subplots`` with linked X axes); a lane has one row per
(level, decoder) pair, drawn as one shade series per colour plus text
labels for the boxes wide enough to hold one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.decode.model import DecodeResult
    from better_scope.decode.session import DecodeSession
    from better_scope.gui.app import BetterScopeApp
    from better_scope.plotting import FrameIndex

FILE_PREFIX = "FILE:"
LEVEL_NAMES = {0: "Bits", 1: "Bytes / words", 2: "Transactions", 3: "Register"}
_LEVEL_SHORT = {0: "bits", 1: "words", 2: "txn", 3: "reg"}
_DEFAULT_LEVELS = {1, 3}
_LEVEL_FILL = {
    0: (160, 160, 160, 70),
    1: (80, 160, 255, 90),
    2: (90, 200, 120, 80),
    3: (240, 180, 60, 90),
}
_ERROR_FILL = (255, 70, 70, 130)
_MAX_LABELS_PER_LANE = 400
_REDRAW_INTERVAL_S = 0.08


@dataclass
class _Row:
    """One (level, decoder) row of a decode lane."""

    label: str
    level: int
    frames: FrameIndex


@dataclass
class _Lane:
    """One bus's decode lane."""

    bus_id: str
    name: str
    rows: list[_Row]


def as_float_arrays(waveforms: dict[str, tuple[Any, Any]]) -> dict[str, tuple[Any, Any]]:
    """Convert acquired ``(t, v)`` pairs to float numpy arrays (worker thread)."""
    import numpy as np

    return {src: (np.asarray(t, dtype=float), np.asarray(v, dtype=float)) for src, (t, v) in waveforms.items()}


class PlotTab:
    """Waveform plot with decode lanes, file save/load, and auto-refresh."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.sources_tag = "plot_sources"
        self.bus_toggles_tag = "plot_bus_toggles"
        self.acquire_btn_tag = "plot_acquire_btn"
        self.auto_tag = "plot_auto_refresh"
        self.hz_tag = "plot_refresh_hz"
        self.status_tag = "plot_status"
        self.area_tag = "plot_area"
        self.save_dialog_tag = "plot_save_dialog"
        self.load_dialog_tag = "plot_load_dialog"

        # Full-resolution data: acquired CHn plus loaded FILE:... sources.
        self.waveforms: dict[str, tuple[Any, Any]] = {}
        self._checked: set[str] = {"CH1"}
        self._file_name = ""
        self._busy = False
        self._last_acquire = 0.0

        # Plot items (recreated by _rebuild_plot).
        self._wave_plot: int | str | None = None
        self._wave_x: int | str | None = None
        self._wave_y: int | str | None = None
        self._series: dict[str, int | str] = {}
        self._lane_axes: list[tuple[_Lane, int | str]] = []

        # Decode display state.
        self._result: DecodeResult | None = None
        self._bus_order: list[tuple[str, str]] = []
        self._lanes: list[_Lane] = []
        self._levels_shown: set[int] = set(_DEFAULT_LEVELS)
        self._hidden_buses: set[str] = set()
        self._themes: dict[Any, int | str] = {}

        # View bookkeeping for tick().
        self._view_key: tuple[float, float, int] | None = None
        self._last_redraw = 0.0
        self._fit_pending = False
        self._fit_y_pending = False
        self._release_x_in = 0

    # -- build -----------------------------------------------------------------

    def build(self, parent: int | str) -> None:
        """Create the controls and an empty plot under ``parent``."""
        with dpg.group(parent=parent):
            dpg.add_text("Select channels, then Acquire, or load a waveform file.", tag=self.status_tag)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Acquire", tag=self.acquire_btn_tag, callback=lambda: self.acquire(),
                               enabled=False)
                dpg.add_button(label="Decode", callback=lambda: self.decode_now())
                dpg.add_checkbox(label="Auto-refresh", tag=self.auto_tag, default_value=False)
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Re-acquire and plot on a timer. Does not decode; use Live decode for that.")
                dpg.add_input_float(
                    label="Hz",
                    tag=self.hz_tag,
                    default_value=float(self.app.scope.config.plot_refresh_hz),
                    width=80,
                    min_value=0.2,
                    max_value=20.0,
                    min_clamped=True,
                    max_clamped=True,
                    callback=self._on_hz_changed,
                )
                dpg.add_spacer(width=20)
                dpg.add_button(label="Save waveforms...", callback=lambda: self._show_save_dialog())
                dpg.add_button(label="Load waveforms...", callback=lambda: self._show_load_dialog())
                dpg.add_button(label="Event Table", callback=lambda: self.app.event_table.show())
            self.app.live_panel.build()
            with dpg.group(horizontal=True):
                dpg.add_text("Sources:")
                dpg.add_group(tag=self.sources_tag, horizontal=True)
            with dpg.group(horizontal=True):
                dpg.add_text("Decode:")
                for level, name in LEVEL_NAMES.items():
                    dpg.add_checkbox(label=name, default_value=level in self._levels_shown,
                                     callback=lambda s, v, lv=level: self._toggle_level(lv, v))
                dpg.add_text("|")
                dpg.add_group(tag=self.bus_toggles_tag, horizontal=True)
            dpg.add_group(tag=self.area_tag)

        with dpg.file_dialog(show=False, tag=self.save_dialog_tag, width=640, height=420, modal=True,
                             callback=self._on_save_path):
            dpg.add_file_extension(".npz")
            dpg.add_file_extension(".csv")
        with dpg.file_dialog(show=False, tag=self.load_dialog_tag, width=640, height=420, modal=True,
                             callback=self._on_load_path):
            dpg.add_file_extension("Waveform files (*.npz *.csv *.txt *.wfm){.npz,.csv,.txt,.wfm}")
            dpg.add_file_extension(".npz")
            dpg.add_file_extension(".csv")

        self._make_themes()
        self._rebuild_plot()

    def _make_themes(self) -> None:
        for key, color in [*_LEVEL_FILL.items(), ("error", _ERROR_FILL)]:
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvShadeSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Fill, color, category=dpg.mvThemeCat_Plots)
            self._themes[key] = theme

    # -- sources ---------------------------------------------------------------

    def _channel_sources(self) -> list[str]:
        return [f"CH{i}" for i in range(1, self.app.scope.analog_channel_count + 1)]

    def _file_sources(self) -> list[str]:
        return [s for s in self.waveforms if s.startswith(FILE_PREFIX)]

    def available_sources(self) -> list[str]:
        """Sources usable for plotting and bus role mapping (channels + loaded)."""
        return self._channel_sources() + self._file_sources()

    def _displayed(self) -> list[str]:
        return [s for s in self.available_sources() if s in self._checked and s in self.waveforms]

    def _rebuild_sources(self) -> None:
        dpg.delete_item(self.sources_tag, children_only=True)
        for source in self.available_sources():
            dpg.add_checkbox(label=source, parent=self.sources_tag, default_value=source in self._checked,
                             callback=lambda s, v, src=source: self._toggle_source(src, v))

    def _toggle_source(self, source: str, value: bool) -> None:
        if value:
            self._checked.add(source)
        else:
            self._checked.discard(source)
        self._rebuild_plot()

    def on_connection_changed(self, connected: bool) -> None:
        """Rebuild the source checkboxes for the connected model."""
        self._rebuild_sources()
        dpg.configure_item(self.acquire_btn_tag, enabled=connected)
        if connected:
            self.set_status(f"{self.app.scope.analog_channel_count} channel(s) available.")
        elif self._file_sources():
            self.set_status(f"Disconnected. Showing {self._file_name}.")
        else:
            self.set_status("Connect to a scope, or load a waveform file.")

    # -- acquisition -----------------------------------------------------------

    def acquire_sources(self) -> list[str]:
        """Channels to transfer: the checked ones plus those the enabled buses need."""
        channels = self._channel_sources()
        sources = [s for s in channels if s in self._checked]
        sources += [s for s in self.app.decode_tab.required_sources() if s in channels and s not in sources]
        return sources

    @property
    def busy(self) -> bool:
        """Whether a Plot-tab acquisition is in progress."""
        return self._busy

    def acquire(self, decode: bool = True) -> None:
        """Acquire :meth:`acquire_sources` on the worker thread.

        Args:
            decode: Decode the new data afterwards (the auto-refresh timer
                only plots).
        """
        if self._busy or self.app.live_panel.visa_busy or not self.app.scope.is_connected():
            return
        sources = self.acquire_sources()
        if not sources:
            self.set_status("Select at least one channel.")
            return

        def work() -> dict[str, tuple[Any, Any]]:
            return as_float_arrays(self.app.scope.acquire_waveforms(sources))

        self._busy = True
        self._last_acquire = time.monotonic()
        self.set_status(f"Acquiring {', '.join(sources)}...")
        self.app.worker.submit(work, lambda waves: self._on_acquired(waves, decode), self._on_error)

    def _on_acquired(self, waveforms: dict[str, tuple[Any, Any]], decode: bool) -> None:
        """Main thread: store the new data, redraw, and optionally decode."""
        self._busy = False
        self.show_acquired(waveforms)
        self.set_status(f"Acquired {len(waveforms)} channel(s).")
        if decode:
            self.app.decode_tab.decode(self.waveforms)

    def show_acquired(self, waveforms: dict[str, tuple[Any, Any]]) -> None:
        """Main thread: replace the acquired (non-file) sources and redraw."""
        before = self._displayed()
        had_data = bool(self.waveforms)
        for source in [s for s in self.waveforms if not s.startswith(FILE_PREFIX)]:
            del self.waveforms[source]
        self.waveforms.update(waveforms)
        if self._displayed() != before:
            self._rebuild_plot()
        else:
            self._view_key = None
        if not had_data:
            self._fit_pending = True

    def decode_now(self) -> None:
        """Acquire and decode when connected; otherwise decode the loaded data."""
        if self.app.scope.is_connected():
            self.acquire()
        elif self.waveforms:
            if not self.app.decode_tab.has_enabled_buses():
                self.set_status("No enabled bus to decode; configure one on the Decode tab.")
                return
            self.app.decode_tab.decode(self.waveforms)
        else:
            self.set_status("Nothing to decode: connect a scope or load a waveform file.")

    def _on_error(self, exc: Exception) -> None:
        self._busy = False
        self.set_status(f"Error: {exc}")

    def set_status(self, text: str) -> None:
        """Show ``text`` on the Plot tab's status line."""
        dpg.set_value(self.status_tag, text)

    # -- save / load -------------------------------------------------------------

    def _show_save_dialog(self) -> None:
        if not self.waveforms:
            self.set_status("Nothing to save yet.")
            return
        cfg = self.app.scope.config
        dpg.configure_item(self.save_dialog_tag, default_path=cfg.get_save_directory(), default_filename="waveforms")
        dpg.show_item(self.save_dialog_tag)

    def _show_load_dialog(self) -> None:
        cfg = self.app.scope.config
        dpg.configure_item(self.load_dialog_tag, default_path=cfg.get_save_directory())
        dpg.show_item(self.load_dialog_tag)

    def _on_save_path(self, sender: int | str, app_data: dict) -> None:
        path = Path(app_data["file_path_name"])
        # Loaded sources are saved under their original names, so a reload
        # gives FILE:CH1 again rather than FILE:FILE:CH1.
        names: dict[str, str] = {}
        for source in self.waveforms:
            plain = source.removeprefix(FILE_PREFIX)
            names[source] = plain if plain == source or plain not in self.waveforms else source.replace(":", "_")
        renamed = {names[s]: w for s, w in self.waveforms.items()}
        session = self.app.decode_tab.session
        bus_dicts = map_dicts = None
        if session is not None:
            copy = session.snapshot()
            copy.remap_sources(names)
            bus_dicts, map_dicts = copy.bus_dicts(), copy.map_dicts()
        self.set_status(f"Saving {path.name}...")
        self.app.worker.submit(
            lambda: self.app.scope.save_waveforms(path, renamed, bus_dicts, map_dicts),
            lambda p: self.set_status(f"Saved {len(renamed)} waveform(s) to {p}"),
            lambda e: self.set_status(f"Save failed: {e}"),
        )

    def _on_load_path(self, sender: int | str, app_data: dict) -> None:
        from better_scope.waveform_io import load_waveforms

        path = Path(app_data["file_path_name"])
        self.set_status(f"Loading {path.name}...")
        self.app.worker.submit(
            lambda: load_waveforms(path),
            lambda loaded: self.load_result(path, *loaded),
            lambda e: self.set_status(f"Load failed: {e}"),
        )

    def load_result(self, path: Path, waveforms: dict[str, tuple[Any, Any]], meta: dict[str, Any]) -> None:
        """Main thread: show loaded waveforms as ``FILE:`` sources and decode them.

        Args:
            path: File they came from.
            waveforms: Source name -> ``(t, v)`` as read from the file.
            meta: File metadata (may carry ``bus_configs``/``map_bindings``).
        """
        for source in self._file_sources():
            del self.waveforms[source]
            self._checked.discard(source)
        mapping = {src: f"{FILE_PREFIX}{src}" for src in waveforms}
        for src, wave in waveforms.items():
            self.waveforms[mapping[src]] = wave
            self._checked.add(mapping[src])
        self._file_name = Path(path).name
        self._rebuild_sources()
        self._rebuild_plot()
        self._fit_pending = True
        self.app.decode_tab.on_sources_changed()
        self.set_status(f"Loaded {self._file_name}: {', '.join(mapping.values())} ({meta.get('format', '?')}).")

        if meta.get("bus_configs") and self.app.decode_tab.ready:
            self._offer_embedded(meta, mapping)
        else:
            self.app.decode_tab.decode(self.waveforms)

    def _offer_embedded(self, meta: dict[str, Any], mapping: dict[str, str]) -> None:
        buses = meta["bus_configs"]
        maps = meta.get("map_bindings") or []

        def close(apply: bool) -> None:
            dpg.delete_item(window)
            if apply:
                self.app.decode_tab.apply_embedded(buses, maps, mapping)
            self.app.decode_tab.decode(self.waveforms)

        names = ", ".join(str(b.get("name", b.get("bus_id"))) for b in buses)
        with dpg.window(label="Bus configs in file", modal=True, no_close=True, autosize=True,
                        pos=(200, 200)) as window:
            dpg.add_text(f"{self._file_name} contains {len(buses)} bus config(s): {names}", wrap=500)
            dpg.add_text("Apply them? This replaces the current buses and register-map bindings.", wrap=500)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Apply", width=100, callback=lambda: close(True))
                dpg.add_button(label="Keep current", width=100, callback=lambda: close(False))

    # -- decode display ----------------------------------------------------------

    def set_decode(self, result: DecodeResult, session: DecodeSession) -> None:
        """Show a decode result as lanes (called on the main thread)."""
        self._result = result
        self._bus_order = [(b.bus_id, b.name) for b in session.buses if b.bus_id in result.frames]
        dpg.delete_item(self.bus_toggles_tag, children_only=True)
        for bus_id, name in self._bus_order:
            dpg.add_checkbox(label=name, parent=self.bus_toggles_tag, default_value=bus_id not in self._hidden_buses,
                             callback=lambda s, v, b=bus_id: self._toggle_bus(b, v))
        self._rebuild_lanes()

    def shown_levels(self) -> list[int]:
        """Levels currently shown in the lanes."""
        return sorted(self._levels_shown)

    def visible_bus_ids(self) -> list[str]:
        """Decoded buses whose lanes are shown."""
        return [b for b, _ in self._bus_order if b not in self._hidden_buses]

    def _toggle_level(self, level: int, value: bool) -> None:
        if value:
            self._levels_shown.add(level)
        else:
            self._levels_shown.discard(level)
        self._rebuild_lanes()

    def _toggle_bus(self, bus_id: str, value: bool) -> None:
        if value:
            self._hidden_buses.discard(bus_id)
        else:
            self._hidden_buses.add(bus_id)
        self._rebuild_lanes()

    def _rebuild_lanes(self) -> None:
        from better_scope.plotting import FrameIndex

        lanes: list[_Lane] = []
        result = self._result
        for bus_id, name in self._bus_order if result is not None else []:
            if bus_id in self._hidden_buses:
                continue
            rows: list[_Row] = []
            for level, bucket in sorted(result.frames.get(bus_id, {}).items()):
                if level not in self._levels_shown:
                    continue
                for decoder_id in dict.fromkeys(f.decoder_id for f in bucket):
                    frames = [f for f in bucket if f.decoder_id == decoder_id]
                    rows.append(_Row(f"{decoder_id} {_LEVEL_SHORT.get(level, level)}", level, FrameIndex(frames)))
            if rows:
                lanes.append(_Lane(bus_id, name, rows))
        self._lanes = lanes
        self._rebuild_plot()

    def center_on(self, start: float, end: float) -> None:
        """Pan (and if needed zoom) the X axis to centre a frame, and show this tab."""
        if self._wave_x is None:
            return
        x_min, x_max = dpg.get_axis_limits(self._wave_x)
        span = x_max - x_min
        width = max(end - start, 1e-12)
        if width * 1.2 > span or width < span / 50:
            span = width * 6
        centre = (start + end) / 2
        dpg.set_axis_limits(self._wave_x, centre - span / 2, centre + span / 2)
        self._release_x_in = 2
        self.app.show_plot_tab()

    # -- plot construction ---------------------------------------------------------

    def _rebuild_plot(self) -> None:
        """Recreate the plot (waveforms + one lane per shown bus), keeping the X range."""
        keep: tuple[float, float] | None = None
        had_content = bool(self._series or self._lane_axes)
        if had_content and self._wave_x is not None and not self._fit_pending:
            keep = tuple(dpg.get_axis_limits(self._wave_x))
        dpg.delete_item(self.area_tag, children_only=True)
        self._series = {}
        self._lane_axes = []

        container: int | str = self.area_tag
        if self._lanes:
            ratios = [4.0] + [0.5 + 0.45 * len(lane.rows) for lane in self._lanes]
            container = dpg.add_subplots(1 + len(self._lanes), 1, parent=self.area_tag, link_all_x=True,
                                         row_ratios=ratios, width=-1, height=-1, no_title=True)

        self._wave_plot = dpg.add_plot(parent=container, width=-1, height=-1, no_title=True)
        dpg.add_plot_legend(parent=self._wave_plot)
        self._wave_x = dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", parent=self._wave_plot)
        self._wave_y = dpg.add_plot_axis(dpg.mvYAxis, label="Voltage (V)", parent=self._wave_plot)
        for source in self._displayed():
            self._series[source] = dpg.add_line_series([], [], label=source, parent=self._wave_y)

        for lane in self._lanes:
            plot = dpg.add_plot(parent=container, width=-1, height=-1, no_title=True, no_menus=True,
                                no_box_select=True)
            dpg.add_plot_axis(dpg.mvXAxis, parent=plot, no_tick_labels=lane is not self._lanes[-1])
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label=lane.name, parent=plot, no_gridlines=True,
                                       no_tick_marks=True)
            dpg.set_axis_limits(y_axis, 0, len(lane.rows))
            dpg.set_axis_ticks(y_axis, tuple((row.label, i + 0.5) for i, row in enumerate(lane.rows)))
            self._lane_axes.append((lane, y_axis))

        if keep is not None:
            dpg.set_axis_limits(self._wave_x, *keep)
            self._release_x_in = 2
        elif self.waveforms or self._lanes:
            self._fit_pending = True
        self._fit_y_pending = True
        self._view_key = None

    def _data_range(self) -> tuple[float, float] | None:
        spans = [(float(self.waveforms[s][0][0]), float(self.waveforms[s][0][-1])) for s in self._displayed()]
        if not spans:
            for lane in self._lanes:
                for row in lane.rows:
                    frames = row.frames.frames
                    if frames:
                        spans.append((frames[0].start, max(f.end for f in frames)))
        if not spans:
            return None
        lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
        return (lo, hi) if hi > lo else (lo - 1e-6, hi + 1e-6)

    # -- per-frame update ------------------------------------------------------------

    def tick(self) -> None:
        """Called once per frame: auto-refresh, axis release, and view redraws."""
        if self._release_x_in:
            self._release_x_in -= 1
            if self._release_x_in == 0 and self._wave_x is not None:
                dpg.set_axis_limits_auto(self._wave_x)

        if self._fit_pending and self._wave_x is not None:
            self._fit_pending = False
            rng = self._data_range()
            if rng is not None:
                dpg.set_axis_limits(self._wave_x, *rng)
                self._release_x_in = 2
                self._redraw(rng[0], rng[1])
        elif self._wave_x is not None:
            now = time.monotonic()
            if now - self._last_redraw >= _REDRAW_INTERVAL_S:
                x_min, x_max = dpg.get_axis_limits(self._wave_x)
                width = int(dpg.get_item_rect_size(self._wave_plot)[0])
                if (x_min, x_max, width) != self._view_key:
                    self._redraw(x_min, x_max)

        if self._busy or not dpg.get_value(self.auto_tag) or not self.app.scope.is_connected():
            return
        if self.app.live_panel.monitor.polling:
            return
        hz = max(0.2, float(dpg.get_value(self.hz_tag) or 1.0))
        if time.monotonic() - self._last_acquire >= 1.0 / hz:
            self.acquire(decode=False)

    def _redraw(self, x_min: float, x_max: float) -> None:
        """Re-decimate the visible waveforms and redraw the lanes for ``[x_min, x_max]``."""
        width = max(int(dpg.get_item_rect_size(self._wave_plot)[0]), 100)
        self._view_key = (x_min, x_max, width)
        self._last_redraw = time.monotonic()
        if x_max <= x_min:
            return
        from better_scope.plotting import minmax_decimate, visible_slice

        for source, series in self._series.items():
            t, v = self.waveforms[source]
            sl = visible_slice(t, x_min, x_max)
            x, y = minmax_decimate(t[sl], v[sl], width)
            dpg.set_value(series, [x, y])
        if self._fit_y_pending and self._series:
            self._fit_y_pending = False
            dpg.fit_axis_data(self._wave_y)
        for lane, y_axis in self._lane_axes:
            self._draw_lane(lane, y_axis, x_min, x_max, width)

    def _draw_lane(self, lane: _Lane, y_axis: int | str, x_min: float, x_max: float, width: int) -> None:
        from better_scope.plotting import box_series, coalesce_spans, fit_label

        dpg.delete_item(y_axis, children_only=True)
        px_per_s = width / (x_max - x_min)
        inset = 1.0 / px_per_s
        labels = 0
        for i, row in enumerate(lane.rows):
            frames = row.frames.visible(x_min, x_max)
            dense = len(frames) > width
            for is_error in (False, True):
                spans = [(f.start, f.end) for f in frames if bool(f.error) == is_error]
                if not spans:
                    continue
                if dense:
                    spans = coalesce_spans(spans, 2 * inset)
                x, y1, y2 = box_series(spans, i + 0.12, i + 0.88, inset=0 if dense else inset)
                series = dpg.add_shade_series(x, y1, y2=y2, parent=y_axis)
                dpg.bind_item_theme(series, self._themes["error" if is_error else row.level])
            if dense:
                continue
            for f in frames:
                if labels >= _MAX_LABELS_PER_LANE:
                    break
                left, right = max(f.start, x_min), min(f.end, x_max)
                text = fit_label(f.text, (right - left) * px_per_s)
                if text is None:
                    continue
                dpg.add_text_point((left + right) / 2, i + 0.5, label=text, parent=y_axis)
                labels += 1

    # -- helpers ---------------------------------------------------------------------

    def _on_hz_changed(self, sender: int | str, value: float) -> None:
        self.app.scope.config.plot_refresh_hz = float(value)
