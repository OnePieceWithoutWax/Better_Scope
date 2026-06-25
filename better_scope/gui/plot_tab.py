"""Plot tab: acquire scaled waveforms and plot them live."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp


class PlotTab:
    """On-demand and auto-refreshing waveform plotting per channel."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.sources_tag = "plot_sources"
        self.acquire_btn_tag = "plot_acquire_btn"
        self.auto_tag = "plot_auto_refresh"
        self.hz_tag = "plot_refresh_hz"
        self.status_tag = "plot_status"
        self.x_axis_tag = "plot_x_axis"
        self.y_axis_tag = "plot_y_axis"

        self._source_checks: list[tuple[str, str | int]] = []  # (source, tag)
        self._series_tags: dict[str, str | int] = {}
        self._busy = False
        self._last_acquire = 0.0

    def build(self, parent: int | str) -> None:
        """Create the controls and an empty plot under ``parent``."""
        with dpg.group(parent=parent):
            dpg.add_text("Select channels, then Acquire.", tag=self.status_tag)
            dpg.add_group(tag=self.sources_tag, horizontal=True)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Acquire",
                    tag=self.acquire_btn_tag,
                    callback=lambda: self.acquire(),
                    enabled=False,
                )
                dpg.add_checkbox(
                    label="Auto-refresh", tag=self.auto_tag, default_value=False
                )
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
            with dpg.plot(label="Waveforms", height=-1, width=-1):
                dpg.add_plot_legend()
                dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=self.x_axis_tag)
                dpg.add_plot_axis(dpg.mvYAxis, label="Voltage (V)", tag=self.y_axis_tag)

    def on_connection_changed(self, connected: bool) -> None:
        """Rebuild the per-channel source checkboxes for the connected model."""
        self._clear_sources()
        self._clear_series()
        if not connected:
            dpg.configure_item(self.acquire_btn_tag, enabled=False)
            dpg.set_value(self.status_tag, "Connect to a scope to plot waveforms.")
            return

        count = self.app.scope.analog_channel_count
        for i in range(1, count + 1):
            source = f"CH{i}"
            tag = f"plot_src_{source}"
            dpg.add_checkbox(
                label=source,
                tag=tag,
                parent=self.sources_tag,
                default_value=(i == 1),
            )
            self._source_checks.append((source, tag))

        dpg.configure_item(self.acquire_btn_tag, enabled=True)
        dpg.set_value(self.status_tag, f"{count} channel(s) available.")

    # -- Acquisition -----------------------------------------------------------

    def _selected_sources(self) -> list[str]:
        return [src for src, tag in self._source_checks if dpg.get_value(tag)]

    def acquire(self) -> None:
        """Trigger a single waveform acquisition (worker thread)."""
        if self._busy or not self.app.scope.is_connected():
            return
        sources = self._selected_sources()
        if not sources:
            dpg.set_value(self.status_tag, "Select at least one channel.")
            return

        self._busy = True
        self._last_acquire = time.monotonic()
        dpg.set_value(self.status_tag, f"Acquiring {', '.join(sources)}...")
        self.app.worker.submit(
            lambda: self.app.scope.acquire_waveforms(sources),
            self._on_acquired,
            self._on_error,
        )

    def _on_acquired(self, waveforms: dict[str, Any]) -> None:
        """Main thread: update the plot series with new data."""
        self._busy = False
        for source, (t_arr, v_arr) in waveforms.items():
            x = [float(v) for v in t_arr]
            y = [float(v) for v in v_arr]
            tag = self._series_tags.get(source)
            if tag is not None and dpg.does_item_exist(tag):
                dpg.set_value(tag, [x, y])
            else:
                tag = f"plot_series_{source}"
                dpg.add_line_series(
                    x, y, label=source, parent=self.y_axis_tag, tag=tag
                )
                self._series_tags[source] = tag

        # Remove series for channels no longer selected.
        for source, tag in list(self._series_tags.items()):
            if source not in waveforms and dpg.does_item_exist(tag):
                dpg.delete_item(tag)
                del self._series_tags[source]

        dpg.fit_axis_data(self.x_axis_tag)
        dpg.fit_axis_data(self.y_axis_tag)
        dpg.set_value(self.status_tag, f"Plotted {len(waveforms)} channel(s).")

    def _on_error(self, exc: Exception) -> None:
        self._busy = False
        dpg.set_value(self.status_tag, f"Error: {exc}")

    def tick(self) -> None:
        """Called once per frame; drives auto-refresh when enabled."""
        if self._busy or not dpg.get_value(self.auto_tag):
            return
        if not self.app.scope.is_connected():
            return
        hz = max(0.2, float(dpg.get_value(self.hz_tag) or 1.0))
        if time.monotonic() - self._last_acquire >= 1.0 / hz:
            self.acquire()

    # -- Helpers ---------------------------------------------------------------

    def _on_hz_changed(self, sender: int | str, value: float) -> None:
        self.app.scope.config.plot_refresh_hz = float(value)

    def _clear_sources(self) -> None:
        self._source_checks = []
        if dpg.does_item_exist(self.sources_tag):
            dpg.delete_item(self.sources_tag, children_only=True)

    def _clear_series(self) -> None:
        for tag in self._series_tags.values():
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._series_tags = {}
