"""Plugin discovery for decoders (and, later, register-map importers).

:class:`Registry` is generic over a plugin base class. It collects subclasses
from three places, in order; a later class with the same ``id`` replaces an
earlier one and logs a warning:

1. Built-in modules in a package (``better_scope/decode/decoders/``).
2. Entry points in a group (``better_scope.decoders``) -- private pip packages.
3. ``.py`` files in ``~/.better_scope/plugins/`` and in every directory listed
   in the ``BETTER_SCOPE_PLUGIN_PATH`` environment variable
   (``os.pathsep``-separated).

A plugin that fails to import or validate is logged, recorded in
:attr:`Registry.errors`, and skipped -- never fatal. Only classes that define
``id`` in their own class body are collected, so helper base classes and
imported classes are ignored.
"""

import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import pkgutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

from better_scope.decode.api import Decoder, validate_decoder

logger = logging.getLogger(__name__)

PLUGIN_PATH_ENV = "BETTER_SCOPE_PLUGIN_PATH"
DECODER_ENTRY_POINT_GROUP = "better_scope.decoders"
REGMAP_IMPORTER_ENTRY_POINT_GROUP = "better_scope.regmap_importers"


def default_user_plugin_dir() -> Path:
    """The per-user plugin folder, ``~/.better_scope/plugins``."""
    return Path.home() / ".better_scope" / "plugins"


def plugin_dirs(user_dir: Path | None = None, env_var: str = PLUGIN_PATH_ENV) -> list[Path]:
    """Plugin folders to scan: the user folder, then each env-var entry.

    Args:
        user_dir: User plugin folder (defaults to :func:`default_user_plugin_dir`).
        env_var: Environment variable holding extra folders.

    Returns:
        Existing directories, in scan order, without duplicates.
    """
    candidates = [user_dir or default_user_plugin_dir()]
    candidates += [Path(p) for p in os.environ.get(env_var, "").split(os.pathsep) if p.strip()]
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


# Plugin files are shared by every registry; load each (path, mtime) once.
_file_modules: dict[Path, tuple[float, ModuleType]] = {}


def load_plugin_file(path: Path) -> ModuleType:
    """Import a plugin ``.py`` file by path (cached per path and mtime).

    Args:
        path: The plugin file.

    Returns:
        The imported module.
    """
    path = path.resolve()
    mtime = path.stat().st_mtime
    cached = _file_modules.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha1(str(path).encode()).hexdigest()[:8]
    name = f"better_scope_plugin_{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and pickling look modules up here
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    _file_modules[path] = (mtime, module)
    return module


class Registry[T]:
    """Discovers and holds plugin classes that subclass ``base``.

    Attributes:
        errors: One message per plugin that was skipped.
        origins: Plugin id -> where it was loaded from.
    """

    def __init__(
        self,
        *,
        kind: str,
        base: type[T],
        builtin_package: str | None,
        entry_point_group: str,
        validate: Callable[[type[T]], list[str]] | None = None,
        validate_all: Callable[[dict[str, type[T]]], dict[str, str]] | None = None,
        user_dir: Path | None = None,
        env_var: str = PLUGIN_PATH_ENV,
    ) -> None:
        """Create an empty registry; call :meth:`discover` to populate it.

        Args:
            kind: Human-readable plugin kind for log messages ("decoder").
            base: Plugin base class.
            builtin_package: Dotted package holding built-in plugin modules.
            entry_point_group: Entry-point group for pip-installed plugins.
            validate: Per-class check returning a list of problems.
            validate_all: Whole-set check returning ``{id: problem}`` for
                classes to drop; re-run until nothing more is dropped.
            user_dir: Override for the user plugin folder (tests).
            env_var: Environment variable listing extra plugin folders.
        """
        self.kind = kind
        self.base = base
        self.builtin_package = builtin_package
        self.entry_point_group = entry_point_group
        self._validate = validate
        self._validate_all = validate_all
        self.user_dir = user_dir
        self.env_var = env_var
        self._classes: dict[str, type[T]] = {}
        self.origins: dict[str, str] = {}
        self.errors: list[str] = []

    # -- discovery -----------------------------------------------------------

    def discover(self) -> "Registry[T]":
        """(Re)scan every source. Returns ``self`` for chaining."""
        self._classes.clear()
        self.origins.clear()
        self.errors.clear()
        if self.builtin_package:
            self._discover_builtins(self.builtin_package)
        self._discover_entry_points()
        for folder in plugin_dirs(self.user_dir, self.env_var):
            for path in sorted(folder.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                try:
                    module = load_plugin_file(path)
                except Exception as e:
                    self._skip(str(path), f"import failed: {e!r}")
                    continue
                self._register_module(module, str(path))
        self._run_validate_all()
        logger.debug(f"{self.kind} registry: {sorted(self._classes)}")
        return self

    def _discover_builtins(self, package_name: str) -> None:
        package = importlib.import_module(package_name)
        for info in pkgutil.iter_modules(package.__path__):
            if info.name.startswith("_"):
                continue
            qualified = f"{package_name}.{info.name}"
            try:
                module = importlib.import_module(qualified)
            except Exception as e:
                self._skip(qualified, f"import failed: {e!r}")
                continue
            self._register_module(module, f"built-in {qualified}")

    def _discover_entry_points(self) -> None:
        for ep in importlib.metadata.entry_points(group=self.entry_point_group):
            origin = f"entry point {ep.name} ({ep.value})"
            try:
                obj = ep.load()
            except Exception as e:
                self._skip(origin, f"import failed: {e!r}")
                continue
            if isinstance(obj, ModuleType):
                self._register_module(obj, origin)
            elif inspect.isclass(obj) and issubclass(obj, self.base):
                self._register(obj, origin)
            else:
                self._skip(origin, f"is neither a module nor a {self.base.__name__} subclass")

    def _register_module(self, module: ModuleType, origin: str) -> None:
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, self.base)
                and obj is not self.base
                and obj.__module__ == module.__name__
                and "id" in vars(obj)
            ):
                self._register(obj, origin)

    def _register(self, cls: type[T], origin: str) -> None:
        label = f"{origin}: {cls.__name__}"
        if self._validate:
            problems = self._validate(cls)
            if problems:
                self._skip(label, "; ".join(problems))
                return
        plugin_id = getattr(cls, "id")
        if plugin_id in self._classes:
            logger.warning(
                f"{self.kind} {plugin_id!r} from {label} overrides the one from {self.origins[plugin_id]}"
            )
        self._classes[plugin_id] = cls
        self.origins[plugin_id] = label

    def _run_validate_all(self) -> None:
        if not self._validate_all:
            return
        while dropped := self._validate_all(dict(self._classes)):
            for plugin_id, problem in dropped.items():
                self._skip(self.origins.get(plugin_id, plugin_id), problem)
                self._classes.pop(plugin_id, None)
                self.origins.pop(plugin_id, None)

    def _skip(self, origin: str, reason: str) -> None:
        message = f"skipped {self.kind} plugin {origin}: {reason}"
        logger.warning(message)
        self.errors.append(message)

    # -- lookup --------------------------------------------------------------

    def get(self, plugin_id: str) -> type[T]:
        """The class registered under ``plugin_id``.

        Raises:
            KeyError: If no such plugin is registered.
        """
        return self._classes[plugin_id]

    def ids(self) -> list[str]:
        """Registered ids, sorted."""
        return sorted(self._classes)

    def items(self) -> list[tuple[str, type[T]]]:
        """``(id, class)`` pairs, sorted by id."""
        return sorted(self._classes.items())

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._classes

    def __iter__(self) -> Iterator[type[T]]:
        return iter([cls for _, cls in self.items()])

    def __len__(self) -> int:
        return len(self._classes)


# -- decoders ----------------------------------------------------------------


def _check_stacking(classes: dict[str, type[Decoder]]) -> dict[str, str]:
    """Drop stacked decoders whose ``stacks_on`` target is missing or cyclic."""
    dropped: dict[str, str] = {}
    for decoder_id, cls in classes.items():
        seen = {decoder_id}
        current = cls
        while current.stacks_on is not None:
            target = current.stacks_on
            if target not in classes:
                dropped[decoder_id] = f"stacks_on {target!r} is not a known decoder"
                break
            if target in seen:
                dropped[decoder_id] = f"stacking cycle through {target!r}"
                break
            seen.add(target)
            current = classes[target]
    return dropped


class DecoderRegistry(Registry[Decoder]):
    """Registry of :class:`Decoder` classes with stack resolution."""

    def __init__(self, *, user_dir: Path | None = None, env_var: str = PLUGIN_PATH_ENV) -> None:
        """Create a decoder registry (call :meth:`discover` to populate).

        Args:
            user_dir: Override for the user plugin folder (tests).
            env_var: Environment variable listing extra plugin folders.
        """
        super().__init__(
            kind="decoder",
            base=Decoder,
            builtin_package="better_scope.decode.decoders",
            entry_point_group=DECODER_ENTRY_POINT_GROUP,
            validate=validate_decoder,
            validate_all=_check_stacking,
            user_dir=user_dir,
            env_var=env_var,
        )

    def chain(self, decoder_id: str) -> list[type[Decoder]]:
        """The stack for ``decoder_id``, root (base decoder) first.

        Raises:
            KeyError: If ``decoder_id`` or a layer below it is unknown.
        """
        chain = [self.get(decoder_id)]
        while chain[0].stacks_on is not None:
            chain.insert(0, self.get(chain[0].stacks_on))
        return chain


_default_registry: DecoderRegistry | None = None


def decoder_registry(refresh: bool = False) -> DecoderRegistry:
    """The shared decoder registry, discovered on first use.

    Args:
        refresh: Re-scan all plugin sources.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = DecoderRegistry().discover()
    elif refresh:
        _default_registry.discover()
    return _default_registry
