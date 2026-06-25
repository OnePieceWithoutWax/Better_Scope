"""Help / About tab: help text, live log viewer, and project info."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

from better_scope.utils import get_resource_path

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

_HELP_FALLBACK = (
    "Better_Scope\n\n"
    "1. Scope tab: scan and connect to a Tektronix oscilloscope.\n"
    "2. Channels tab: read and rename channel labels.\n"
    "3. Plot tab: acquire and plot live waveforms.\n"
    "4. Capture tab: save a screenshot (Basic or Engineering layout).\n"
    "5. Config tab: filename and output options.\n"
)


class HelpAboutTab:
    """Static help, a scrolling log view, and about information."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.log_tag = "help_log_text"
        self._log_lines: list[str] = []

    def build(self, parent: int | str) -> None:
        """Create help text, log viewer, and about info under ``parent``."""
        with dpg.group(parent=parent):
            with dpg.collapsing_header(label="Help", default_open=True):
                dpg.add_text(self._help_text(), wrap=900)

            with dpg.collapsing_header(label="Application Log"):
                dpg.add_button(label="Save Log", callback=lambda: self._save_log())
                with dpg.child_window(height=240, autosize_x=True):
                    dpg.add_text("", tag=self.log_tag)

            with dpg.collapsing_header(label="About"):
                dpg.add_text(f"Better_Scope version {self.app.scope.version}")
                dpg.add_text("Author: Niel Walker")
                dpg.add_text("Email: nielandrewalker@gmail.com")

        # Stream log records into the viewer (posted to the main thread).
        self.app.scope.log_handler.add_callback(self._on_log_record)
        self._render_existing_log()

    def _help_text(self) -> str:
        help_file = get_resource_path("Better_Scope_Help.md")
        try:
            if help_file.exists():
                return help_file.read_text(encoding="utf-8")
        except Exception:
            pass
        return _HELP_FALLBACK

    def _on_log_record(self, record: logging.LogRecord) -> None:
        """Log callback (any thread): marshal formatting to the main thread."""
        formatted = self.app.scope.log_handler.format(record)
        self.app.worker.post(lambda: self._append_log(formatted))

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        # Cap the rendered history to keep the widget responsive.
        self._log_lines = self._log_lines[-500:]
        if dpg.does_item_exist(self.log_tag):
            dpg.set_value(self.log_tag, "\n".join(self._log_lines))

    def _render_existing_log(self) -> None:
        self._log_lines = list(self.app.scope.log_handler.entries)[-500:]
        dpg.set_value(self.log_tag, "\n".join(self._log_lines))

    def _save_log(self) -> None:
        try:
            path = self.app.scope.save_log()
            self._append_log(f"Log saved to: {path}")
        except Exception as e:
            self._append_log(f"Failed to save log: {e}")
