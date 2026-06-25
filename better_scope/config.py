"""Persistent application configuration for Better_Scope."""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_save_directory() -> str:
    """Default capture directory: ``~/Pictures/scope_capture``."""
    return str(Path.home() / "Pictures" / "scope_capture")


@dataclass
class AppConfig:
    """Configuration manager with JSON persistence and auto-save.

    Settings are written to ``~/.better_scope/config.json`` whenever a public
    field is assigned (after initial load). ``auto_increment`` and
    ``datestamp`` are mutually exclusive.
    """

    # Persisted fields.
    save_directory: str = field(default_factory=_default_save_directory)
    default_filename: str = "capture"
    file_format: str = "png"
    background_color: str = "white"
    save_waveform: bool = False
    auto_increment: bool = False
    datestamp: bool = True
    last_used_metadata: dict = field(default_factory=dict)
    recent_directories: list = field(default_factory=list)
    display_captured_image: str = "Disabled"  # "Disabled" | "Display To The Right" | "Display Below"
    display_image_size: str = "Medium"  # "Small" | "Medium" | "Large"
    auto_copy_to_clipboard: bool = False
    last_connected_scope: dict = field(default_factory=dict)
    plot_refresh_hz: float = 3.0

    # Non-persisted fields (excluded from JSON).
    _config_file: Path | None = field(default=None, repr=False, compare=False)
    _app_data_dir: Path | None = field(default=None, repr=False, compare=False)
    _loading: bool = field(default=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Resolve paths and load any existing config from disk."""
        object.__setattr__(self, "_app_data_dir", Path.home() / ".better_scope")
        if self._config_file is None:
            object.__setattr__(self, "_config_file", self._app_data_dir / "config.json")
        self._load_config()
        object.__setattr__(self, "_loading", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a field, enforcing mutual exclusivity and auto-saving."""
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return

        if name == "auto_increment" and value:
            object.__setattr__(self, "datestamp", False)
        elif name == "datestamp" and value:
            object.__setattr__(self, "auto_increment", False)

        object.__setattr__(self, name, value)

        if not getattr(self, "_loading", True):
            self.save_config()

    @property
    def formatted_file_format(self) -> str:
        """File format with a leading dot (e.g. ``.png``)."""
        fmt = self.file_format
        return fmt if fmt.startswith(".") else f".{fmt}"

    @property
    def default_save_directory(self) -> str:
        """The default save directory path."""
        return _default_save_directory()

    def _load_config(self) -> None:
        """Load configuration from disk if the file exists."""
        try:
            if self._config_file and self._config_file.exists():
                with open(self._config_file, encoding="utf-8") as f:
                    loaded = json.load(f)
                for key, value in loaded.items():
                    if hasattr(self, key) and not key.startswith("_"):
                        object.__setattr__(self, key, value)
                logger.debug(f"Loaded config ({len(loaded)} keys) from {self._config_file}")
            else:
                logger.debug(f"No existing config at {self._config_file}")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")

    def save_config(self) -> None:
        """Persist configuration to disk as JSON."""
        if self._config_file is None:
            return
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            config_dict = {
                k: v for k, v in asdict(self).items() if not k.startswith("_")
            }
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=4)
            logger.debug(f"Config saved to {self._config_file}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def get_save_directory(self) -> str:
        """Return the save directory, creating it if necessary."""
        dir_path = Path(self.save_directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        return str(dir_path)

    def get_default_save_directory(self) -> str:
        """Return the default save directory path."""
        return self.default_save_directory

    def set_save_directory(self, directory: str | Path) -> None:
        """Set the save directory and update the recent-directories list."""
        directory = str(directory)
        if directory not in self.recent_directories:
            self.recent_directories.insert(0, directory)
            object.__setattr__(self, "recent_directories", self.recent_directories[:5])
        self.save_directory = directory
