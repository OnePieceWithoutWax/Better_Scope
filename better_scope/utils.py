"""Utility helpers for Better_Scope (paths, filenames, resources)."""

import datetime
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def get_resource_path(relative_path: str) -> Path:
    """Resolve a path inside the bundled ``resources`` directory.

    Works both in development and when frozen by PyInstaller.

    Args:
        relative_path: Path relative to the ``resources`` directory.

    Returns:
        Absolute path to the resource.
    """
    if getattr(sys, "frozen", False):
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base_path = Path(__file__).parent.parent

    return base_path / "resources" / relative_path


def open_file_explorer(path: str | Path) -> None:
    """Open the platform file explorer at ``path`` (creating it if needed)."""
    path_obj = Path(path)
    if not path_obj.exists():
        path_obj.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Windows":
        os.startfile(str(path_obj))  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path_obj)], check=False)
    else:
        subprocess.run(["xdg-open", str(path_obj)], check=False)


def filename_with_suffix(filename: str, suffix: str) -> str:
    """Append ``suffix`` (an extension) to ``filename`` if not already present.

    Args:
        filename: Base filename.
        suffix: Extension such as ``"png"`` or ``".png"``.

    Returns:
        Filename guaranteed to end with the dotted suffix.
    """
    if not suffix.startswith("."):
        suffix = "." + suffix
    if filename.endswith(suffix):
        return filename
    return filename + suffix


def get_next_incremented_filename(
    directory: str | Path, base_filename: str, suffix: str
) -> str:
    """Return the next free ``base_filename_NNN.ext`` in ``directory``.

    Args:
        directory: Directory checked for existing files.
        base_filename: Base filename without the incrementor.
        suffix: Extension such as ``"png"`` or ``".png"``.

    Returns:
        A filename (no path) that does not yet exist in ``directory``.
    """
    directory = Path(directory)
    stem = Path(base_filename).stem if suffix in base_filename else Path(base_filename)
    ext = suffix if suffix.startswith(".") else "." + suffix

    counter = 1
    while True:
        incrementor = str(counter).zfill(3) if counter < 1000 else str(counter)
        new_filename = f"{stem}_{incrementor}{ext}"
        if not (directory / new_filename).exists():
            return new_filename
        counter += 1


def get_filename_with_datestamp(
    directory: str | Path, base_filename: str, suffix: str
) -> str:
    """Return ``base_filename_YYYY.MM.DD_HH.MM.SS.ext``, avoiding collisions.

    Args:
        directory: Directory checked for existing files.
        base_filename: Base filename without the datestamp.
        suffix: Extension such as ``"png"`` or ``".png"``.

    Returns:
        A unique timestamped filename (no path).

    Raises:
        ValueError: If a unique name could not be generated after 100 attempts.
    """
    directory = Path(directory)
    stem = Path(base_filename).stem
    ext = suffix if suffix.startswith(".") else "." + suffix

    for _ in range(100):
        timestamp = datetime.datetime.now().strftime("%Y.%m.%d_%H.%M.%S")
        new_filename = f"{stem}_{timestamp}{ext}"
        if not (directory / new_filename).exists():
            return new_filename
        time.sleep(0.1)

    raise ValueError("Could not generate a unique filename after 100 attempts")
