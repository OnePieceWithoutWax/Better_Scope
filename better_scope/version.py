"""Application version resolution for Better_Scope.

Resolves the version from the installed package metadata, falling back to a
``git describe`` during source runs, then to the hard-coded default.
"""

import logging
import subprocess
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

logger = logging.getLogger(__name__)

_FALLBACK_VERSION = "2.1.0"
_version_source: str = "unknown"


def _version_from_metadata() -> str | None:
    """Return the installed package version, or ``None`` if not installed."""
    try:
        return _pkg_version("better-scope")
    except PackageNotFoundError:
        return None


def _version_from_git() -> str | None:
    """Return ``git describe --tags --always`` output, or ``None`` on failure."""
    git_dir = Path.cwd().resolve()
    while not (git_dir / ".git").exists():
        if git_dir.parent == git_dir:
            return None
        git_dir = git_dir.parent

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=git_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    tag = result.stdout.strip()
    return tag[1:] if tag.startswith("v") else tag


def get_version() -> str:
    """Resolve the application version string.

    Returns:
        The version from package metadata, git, or the fallback default.
    """
    global _version_source

    resolved = _version_from_metadata()
    if resolved:
        _version_source = "metadata"
        return resolved

    resolved = _version_from_git()
    if resolved:
        _version_source = "git"
        return resolved

    _version_source = "fallback"
    return _FALLBACK_VERSION


def log_version_info() -> None:
    """Log how the version was resolved (call after the logger is configured)."""
    logger.info(f"Version resolved from {_version_source}: {__version__}")


__version__ = get_version()
