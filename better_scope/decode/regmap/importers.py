"""Register-map importer plugins.

An importer turns one file into a :class:`~better_scope.decode.regmap.model.Device`.
Importers are discovered like decoders (see ``decode/registry.py``): built-in
modules in this package, the ``better_scope.regmap_importers`` entry-point
group, and ``.py`` files in the plugin folders. :func:`load_register_map`
picks the importer by file extension.
"""

from pathlib import Path
from typing import ClassVar

from better_scope.decode.regmap.model import Device
from better_scope.decode.registry import PLUGIN_PATH_ENV, REGMAP_IMPORTER_ENTRY_POINT_GROUP, Registry


class RegmapImporter:
    """Base class for register-map importers.

    Subclasses set ``id``, ``name`` and ``extensions`` and implement
    :meth:`load`. Report bad input by raising
    :class:`~better_scope.decode.regmap.model.RegmapError` with every
    problem found, located by sheet/row/column where the format has them.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    # Lower-case file extensions, with the dot (".xlsx").
    extensions: ClassVar[tuple[str, ...]] = ()

    def load(self, path: Path) -> Device:
        """Read ``path`` into a :class:`Device`.

        Args:
            path: The register-map file.

        Returns:
            The device and its registers.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement load()")


def validate_importer(cls: type[RegmapImporter]) -> list[str]:
    """Check an importer class's metadata; returns problems (empty if valid)."""
    problems: list[str] = []
    for attr in ("id", "name"):
        value = getattr(cls, attr, None)
        if not isinstance(value, str) or not value:
            problems.append(f"missing or empty class attribute {attr!r}")
    if not cls.extensions or not all(isinstance(e, str) and e.startswith(".") for e in cls.extensions):
        problems.append("extensions must be a non-empty tuple like ('.xlsx',)")
    if cls.load is RegmapImporter.load:
        problems.append("importer must implement load()")
    return problems


class ImporterRegistry(Registry[RegmapImporter]):
    """Registry of :class:`RegmapImporter` classes."""

    def __init__(self, *, user_dir: Path | None = None, env_var: str = PLUGIN_PATH_ENV) -> None:
        """Create an importer registry (call :meth:`discover` to populate).

        Args:
            user_dir: Override for the user plugin folder (tests).
            env_var: Environment variable listing extra plugin folders.
        """
        super().__init__(
            kind="register-map importer",
            base=RegmapImporter,
            builtin_package="better_scope.decode.regmap",
            entry_point_group=REGMAP_IMPORTER_ENTRY_POINT_GROUP,
            validate=validate_importer,
            user_dir=user_dir,
            env_var=env_var,
        )

    def for_path(self, path: Path) -> type[RegmapImporter]:
        """The importer that handles ``path``'s extension.

        Raises:
            KeyError: If no importer claims the extension.
        """
        suffix = Path(path).suffix.lower()
        for cls in self:
            if suffix in cls.extensions:
                return cls
        raise KeyError(f"no register-map importer for {suffix or 'files without an extension'!r}")


_default_registry: ImporterRegistry | None = None


def importer_registry(refresh: bool = False) -> ImporterRegistry:
    """The shared importer registry, discovered on first use.

    Args:
        refresh: Re-scan all plugin sources.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ImporterRegistry().discover()
    elif refresh:
        _default_registry.discover()
    return _default_registry


def load_register_map(path: Path, registry: ImporterRegistry | None = None) -> Device:
    """Load a register map with the importer for its file extension.

    Args:
        path: The register-map file.
        registry: Importer registry (defaults to the shared one).

    Returns:
        The loaded device.

    Raises:
        KeyError: If no importer handles the extension.
        RegmapError: If the file has problems.
    """
    path = Path(path)
    importer = (registry or importer_registry()).for_path(path)
    return importer().load(path)
