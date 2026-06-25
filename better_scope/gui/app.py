"""DearPyGui application shell: viewport, tab bar, and render loop."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from better_scope.core import BetterScope
from better_scope.gui.capture_tab import CaptureTab
from better_scope.gui.channels_tab import ChannelsTab
from better_scope.gui.config_tab import ConfigTab
from better_scope.gui.help_about_tab import HelpAboutTab
from better_scope.gui.plot_tab import PlotTab
from better_scope.gui.scope_tab import ScopeTab
from better_scope.gui.trigger_tab import TriggerTab
from better_scope.gui.worker import Worker

PRIMARY_WINDOW = "primary_window"


class BetterScopeApp:
    """Top-level DearPyGui application wiring the backend to the tabs."""

    def __init__(self) -> None:
        self.scope = BetterScope()
        self.worker = Worker()

        self.scope_tab = ScopeTab(self)
        self.capture_tab = CaptureTab(self)
        self.channels_tab = ChannelsTab(self)
        self.trigger_tab = TriggerTab(self)
        self.plot_tab = PlotTab(self)
        self.config_tab = ConfigTab(self)
        self.help_about_tab = HelpAboutTab(self)

    # -- Shared helpers --------------------------------------------------------

    def on_connection_changed(self) -> None:
        """Notify every tab that the connection state changed.

        Always invoked on the main thread (via the worker queue), so tabs may
        safely create or delete widgets here.
        """
        connected = self.scope.is_connected()
        self.scope_tab.on_connection_changed(connected)
        self.channels_tab.on_connection_changed(connected)
        self.trigger_tab.on_connection_changed(connected)
        self.plot_tab.on_connection_changed(connected)

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Create all windows, tabs and the texture registry."""
        with dpg.window(tag=PRIMARY_WINDOW):
            with dpg.tab_bar():
                with dpg.tab(label="Scope"):
                    self.scope_tab.build(dpg.last_item())
                with dpg.tab(label="Capture"):
                    self.capture_tab.build(dpg.last_item())
                with dpg.tab(label="Channels"):
                    self.channels_tab.build(dpg.last_item())
                with dpg.tab(label="Trigger"):
                    self.trigger_tab.build(dpg.last_item())
                with dpg.tab(label="Plot"):
                    self.plot_tab.build(dpg.last_item())
                with dpg.tab(label="Config"):
                    self.config_tab.build(dpg.last_item())
                with dpg.tab(label="Help / About"):
                    self.help_about_tab.build(dpg.last_item())

    def run(self) -> None:
        """Create the context, show the viewport and run the render loop."""
        dpg.create_context()
        dpg.create_viewport(title="Better Scope", width=1100, height=720)

        self.build()

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window(PRIMARY_WINDOW, True)

        # Auto-scan shortly after launch so the UI is already visible.
        self.scope_tab.start_scan()

        while dpg.is_dearpygui_running():
            self.worker.drain()
            self.plot_tab.tick()
            dpg.render_dearpygui_frame()

        dpg.destroy_context()


def main() -> None:
    """Entry point for ``better-scope`` / ``python main.py``."""
    BetterScopeApp().run()


if __name__ == "__main__":
    main()
