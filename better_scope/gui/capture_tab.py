"""Capture tab: Basic and Engineering screenshot layouts."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

_SIZE_MAP = {"Small": 512, "Medium": 800, "Large": 1024}


class CaptureTab:
    """Screenshot capture with selectable subdirectory-building layouts."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.layout_tag = "capture_layout"
        self.content_tag = "capture_content"
        self.status_tag = "capture_status"
        self.save_dir_tag = "capture_save_dir"
        self.filename_tag = "capture_filename"
        self.image_group_tag = "capture_image_group"
        self.texture_registry_tag = "capture_texture_registry"

        # Engineering subdirectory rows: each row tracks its label and the tags
        # of its input fields plus the "+" button to insert new fields before.
        self._subdir_rows: list[dict[str, Any]] = []
        self._uid = 0
        self._cur_texture: str | int | None = None
        self._cur_image: str | int | None = None

    # -- Build -----------------------------------------------------------------

    def build(self, parent: int | str) -> None:
        """Create the layout selector, content area and image display."""
        dpg.add_texture_registry(tag=self.texture_registry_tag)
        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("Layout:")
                dpg.add_radio_button(
                    items=["Basic", "Engineering"],
                    tag=self.layout_tag,
                    default_value="Basic",
                    horizontal=True,
                    callback=lambda: self._draw_content(),
                )
            dpg.add_separator()
            dpg.add_group(tag=self.content_tag)
            dpg.add_text("", tag=self.status_tag)
            dpg.add_group(tag=self.image_group_tag)

        # File dialog used by both layouts' "Browse" buttons.
        with dpg.file_dialog(
            directory_selector=True,
            show=False,
            tag="capture_dir_dialog",
            width=600,
            height=400,
            callback=self._on_dir_selected,
        ):
            pass

        self._draw_content()

    def _next_uid(self) -> int:
        self._uid += 1
        return self._uid

    def _draw_content(self) -> None:
        """Rebuild the content area for the currently selected layout."""
        dpg.delete_item(self.content_tag, children_only=True)
        self._subdir_rows = []
        if dpg.get_value(self.layout_tag) == "Engineering":
            self._draw_engineering()
        else:
            self._draw_basic()

    # -- Basic layout ----------------------------------------------------------

    def _draw_basic(self) -> None:
        cfg = self.app.scope.config
        with dpg.group(parent=self.content_tag):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Browse", callback=lambda: dpg.show_item("capture_dir_dialog"))
                dpg.add_input_text(
                    tag=self.save_dir_tag,
                    default_value=cfg.get_save_directory(),
                    width=560,
                    hint="Save directory",
                )
            dpg.add_input_text(
                tag=self.filename_tag,
                default_value=cfg.default_filename,
                width=300,
                hint="Filename",
            )
            self._capture_buttons()

    # -- Engineering layout ----------------------------------------------------

    def _draw_engineering(self) -> None:
        cfg = self.app.scope.config
        default_dir, subdirs = self._split_save_directory()
        with dpg.group(parent=self.content_tag):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Browse", callback=lambda: dpg.show_item("capture_dir_dialog"))
                dpg.add_input_text(
                    tag=self.save_dir_tag, default_value=default_dir, width=560
                )
            dpg.add_separator()
            dpg.add_text("Subdirectories:")
            self._rows_container = dpg.add_group()
            ic_value = subdirs[0] if len(subdirs) > 0 else "Unknown"
            test_value = subdirs[1] if len(subdirs) > 1 else "test"
            self._add_subdir_row("IC Part Number:", ic_value)
            self._add_subdir_row("Test:", test_value)
            dpg.add_separator()
            dpg.add_input_text(
                tag=self.filename_tag,
                default_value=cfg.default_filename,
                width=300,
                hint="Filename",
            )
            self._capture_buttons()

    def _add_subdir_row(self, label: str, default_value: str = "") -> None:
        """Add a labeled subdirectory row with one field and +/- buttons."""
        row = dpg.add_group(horizontal=True, parent=self._rows_container)
        dpg.add_text(label, parent=row)
        plus_tag = f"subdir_plus_{self._next_uid()}"
        first_field = f"subdir_field_{self._next_uid()}"
        dpg.add_input_text(
            tag=first_field, default_value=default_value, width=160, parent=row
        )
        row_data: dict[str, Any] = {"row": row, "fields": [first_field], "plus": plus_tag}
        dpg.add_button(
            label="+", width=24, tag=plus_tag, parent=row,
            callback=lambda: self._add_field(row_data),
        )
        dpg.add_button(
            label="-", width=24, parent=row,
            callback=lambda: self._remove_field(row_data),
        )
        self._subdir_rows.append(row_data)

    def _add_field(self, row_data: dict[str, Any]) -> None:
        field_tag = f"subdir_field_{self._next_uid()}"
        dpg.add_input_text(
            tag=field_tag, width=160, parent=row_data["row"], before=row_data["plus"]
        )
        row_data["fields"].append(field_tag)

    def _remove_field(self, row_data: dict[str, Any]) -> None:
        if len(row_data["fields"]) <= 1:
            return
        field_tag = row_data["fields"].pop()
        if dpg.does_item_exist(field_tag):
            dpg.delete_item(field_tag)

    def _subdir_path(self) -> Path:
        """Join each row's fields with ``_`` and rows into a relative path."""
        parts: list[str] = []
        for row_data in self._subdir_rows:
            values = [
                dpg.get_value(f).strip()
                for f in row_data["fields"]
                if dpg.get_value(f).strip()
            ]
            if values:
                parts.append("_".join(values))
        return Path(*parts) if parts else Path()

    def _split_save_directory(self) -> tuple[str, list[str]]:
        """Split the configured save dir into (default_dir, subdir parts)."""
        cfg = self.app.scope.config
        default_dir = Path(cfg.get_default_save_directory())
        current = Path(cfg.save_directory)
        try:
            subdirs = list(current.relative_to(default_dir).parts)
        except ValueError:
            subdirs = []
        return str(default_dir), subdirs

    # -- Shared capture controls ----------------------------------------------

    def _capture_buttons(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(label="Capture", callback=lambda: self._capture())
            dpg.add_button(label="Copy to Clipboard", callback=lambda: self._copy())

    def _on_dir_selected(self, sender: int | str, app_data: dict[str, Any]) -> None:
        path = app_data.get("file_path_name") or app_data.get("current_path")
        if path:
            dpg.set_value(self.save_dir_tag, path)

    def _capture(self) -> None:
        """Validate inputs and run a capture on the worker thread."""
        if not self.app.scope.is_connected():
            dpg.set_value(self.status_tag, "No oscilloscope connected.")
            return

        base_dir = Path(dpg.get_value(self.save_dir_tag))
        if dpg.get_value(self.layout_tag) == "Engineering":
            save_dir = base_dir / self._subdir_path()
        else:
            save_dir = base_dir

        base_filename = dpg.get_value(self.filename_tag) or "capture"
        suffix = self.app.scope.config.formatted_file_format

        def _do() -> bytes:
            save_dir.mkdir(parents=True, exist_ok=True)
            filename = self.app.scope.get_capture_filename(
                str(save_dir), base_filename, suffix
            )
            return self.app.scope.capture(
                save_dir=str(save_dir), filename=Path(filename).stem, suffix=suffix
            )

        dpg.set_value(self.status_tag, "Capturing...")
        self.app.worker.submit(_do, self._on_captured, self._on_error)

    def _on_captured(self, image_bytes: bytes) -> None:
        dpg.set_value(self.status_tag, f"Captured ({len(image_bytes)} bytes).")
        if self.app.scope.config.display_captured_image != "Disabled":
            self._show_image(image_bytes)

    def _copy(self) -> None:
        if not self.app.scope.recent.get("screenshot_data"):
            dpg.set_value(self.status_tag, "Nothing captured yet.")
            return
        self.app.worker.submit(
            self.app.scope.copy_to_clipboard,
            lambda ok: dpg.set_value(
                self.status_tag, "Copied to clipboard." if ok else "Clipboard failed."
            ),
            self._on_error,
        )

    def _on_error(self, exc: Exception) -> None:
        dpg.set_value(self.status_tag, f"Error: {exc}")

    # -- Image display ---------------------------------------------------------

    def _show_image(self, image_bytes: bytes) -> None:
        """Render captured PNG bytes into a dpg texture (main thread)."""
        try:
            import numpy as np
            from PIL import Image
        except ImportError as e:
            dpg.set_value(self.status_tag, f"Image display unavailable: {e}")
            return

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        except Exception as e:
            dpg.set_value(self.status_tag, f"Could not decode image: {e}")
            return

        max_dim = _SIZE_MAP.get(self.app.scope.config.display_image_size, 800)
        image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        width, height = image.size
        data = (np.asarray(image, dtype=np.float32) / 255.0).ravel()

        # Replace any previously shown image/texture.
        if self._cur_image is not None and dpg.does_item_exist(self._cur_image):
            dpg.delete_item(self._cur_image)
        if self._cur_texture is not None and dpg.does_item_exist(self._cur_texture):
            dpg.delete_item(self._cur_texture)

        tex_tag = f"capture_tex_{self._next_uid()}"
        dpg.add_static_texture(
            width, height, data, parent=self.texture_registry_tag, tag=tex_tag
        )
        img_tag = dpg.add_image(tex_tag, parent=self.image_group_tag)
        self._cur_texture = tex_tag
        self._cur_image = img_tag
