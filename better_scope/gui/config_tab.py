"""Config tab: persistent capture/output settings bound to ``AppConfig``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

_DISPLAY_MODES = ["Disabled", "Display To The Right", "Display Below"]
_DISPLAY_SIZES = ["Small", "Medium", "Large"]


class ConfigTab:
    """Editable application settings persisted via :class:`AppConfig`."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.auto_inc_tag = "cfg_auto_increment"
        self.datestamp_tag = "cfg_datestamp"
        self.auto_copy_tag = "cfg_auto_copy"
        self.display_tag = "cfg_display_mode"
        self.size_tag = "cfg_display_size"
        self.format_tag = "cfg_file_format"

    @property
    def _config(self):
        return self.app.scope.config

    def build(self, parent: int | str) -> None:
        """Create the settings widgets under ``parent``."""
        cfg = self._config
        with dpg.group(parent=parent):
            dpg.add_combo(
                label="File format",
                items=["png"],
                default_value=cfg.file_format,
                width=120,
                tag=self.format_tag,
                callback=lambda s, v: setattr(cfg, "file_format", v),
            )
            dpg.add_checkbox(
                label="Auto increment filename",
                tag=self.auto_inc_tag,
                default_value=cfg.auto_increment,
                callback=self._on_auto_increment,
            )
            dpg.add_checkbox(
                label="Append datestamp to filename",
                tag=self.datestamp_tag,
                default_value=cfg.datestamp,
                callback=self._on_datestamp,
            )
            dpg.add_checkbox(
                label="Auto copy to clipboard after capture",
                tag=self.auto_copy_tag,
                default_value=cfg.auto_copy_to_clipboard,
                callback=lambda s, v: setattr(cfg, "auto_copy_to_clipboard", v),
            )
            dpg.add_separator()
            dpg.add_combo(
                label="Display captured image",
                items=_DISPLAY_MODES,
                default_value=cfg.display_captured_image,
                width=200,
                tag=self.display_tag,
                callback=lambda s, v: setattr(cfg, "display_captured_image", v),
            )
            dpg.add_combo(
                label="Display image size",
                items=_DISPLAY_SIZES,
                default_value=cfg.display_image_size,
                width=120,
                tag=self.size_tag,
                callback=lambda s, v: setattr(cfg, "display_image_size", v),
            )

    def _on_auto_increment(self, sender: int | str, value: bool) -> None:
        """Set auto-increment and reflect mutual exclusivity in the UI."""
        self._config.auto_increment = value
        dpg.set_value(self.datestamp_tag, self._config.datestamp)

    def _on_datestamp(self, sender: int | str, value: bool) -> None:
        """Set datestamp and reflect mutual exclusivity in the UI."""
        self._config.datestamp = value
        dpg.set_value(self.auto_inc_tag, self._config.auto_increment)
