"""A decode setup: bus configs plus register-map bindings, run as one step.

:class:`DecodeSession` is what the GUI persists and what a saved waveform
file embeds. It stores map *paths*, loads them on demand (cached by file
modification time) and runs :func:`~better_scope.decode.engine.run` followed
by :func:`~better_scope.decode.regmap.annotate`. GUI-agnostic::

    session = DecodeSession.from_dicts(cfg.decode_buses, cfg.decode_maps)
    outcome = session.decode(waveforms)
    outcome.result, outcome.accesses
"""

import copy
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Self

from better_scope.decode.engine import Waveform, run
from better_scope.decode.model import BusConfig, DecodeResult
from better_scope.decode.registry import DecoderRegistry, decoder_registry
from better_scope.decode.regmap.importers import load_register_map
from better_scope.decode.regmap.mapper import DeviceBinding, RegisterAccess, SpiLayout, annotate
from better_scope.decode.regmap.model import Device


@dataclass
class MapBinding:
    """Persisted link from a register-map file to a bus.

    Attributes:
        bus_id: Bus the device sits on.
        path: Register-map file (Excel). Empty means the PMBus standard
            command table only.
        address: SMBus 7-bit address; ``None`` uses the map's own address.
        spi_layout: :class:`SpiLayout` fields for SPI buses (defaults if empty).
    """

    bus_id: str
    path: str = ""
    address: int | None = None
    spi_layout: dict[str, Any] = field(default_factory=dict)

    def layout(self) -> SpiLayout:
        """The SPI layout (raises ``ValueError`` if the stored fields are invalid)."""
        known = {f.name for f in fields(SpiLayout)}
        return SpiLayout(**{k: v for k, v in self.spi_layout.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild from :meth:`to_dict` output."""
        address = data.get("address")
        return cls(
            bus_id=str(data["bus_id"]),
            path=str(data.get("path", "")),
            address=None if address is None else int(address),
            spi_layout=dict(data.get("spi_layout") or {}),
        )


@dataclass
class DecodeOutcome:
    """Result of :meth:`DecodeSession.decode`.

    Attributes:
        result: Frames of every bus, including level-3 register frames.
        accesses: Register command history, sorted by start time.
        map_errors: Binding index -> why its map could not be used.
    """

    result: DecodeResult
    accesses: list[RegisterAccess]
    map_errors: dict[int, str] = field(default_factory=dict)


class _DeviceCache:
    """Loads register maps, re-reading a file only when it changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Device]] = {}

    def load(self, path: str) -> Device:
        p = Path(path)
        mtime = p.stat().st_mtime
        with self._lock:
            hit = self._cache.get(path)
            if hit is not None and hit[0] == mtime:
                return hit[1]
        device = load_register_map(p)
        with self._lock:
            self._cache[path] = (mtime, device)
        return device


_devices = _DeviceCache()


def new_bus_id() -> str:
    """A fresh unique bus id."""
    return uuid.uuid4().hex[:8]


@dataclass
class DecodeSession:
    """Bus configs and register-map bindings.

    Attributes:
        buses: Bus configurations, in display order.
        maps: Register-map bindings.
    """

    buses: list[BusConfig] = field(default_factory=list)
    maps: list[MapBinding] = field(default_factory=list)

    @classmethod
    def from_dicts(cls, buses: Iterable[Mapping[str, Any]], maps: Iterable[Mapping[str, Any]] = ()) -> Self:
        """Rebuild from :meth:`bus_dicts` / :meth:`map_dicts` output."""
        return cls([BusConfig.from_dict(dict(b)) for b in buses], [MapBinding.from_dict(m) for m in maps])

    def bus_dicts(self) -> list[dict[str, Any]]:
        """Bus configs as JSON-ready dicts."""
        return [b.to_dict() for b in self.buses]

    def map_dicts(self) -> list[dict[str, Any]]:
        """Map bindings as JSON-ready dicts."""
        return [m.to_dict() for m in self.maps]

    def snapshot(self) -> Self:
        """A deep copy, safe to hand to a worker thread."""
        return copy.deepcopy(self)

    def bus(self, bus_id: str) -> BusConfig | None:
        """The bus with ``bus_id``, if any."""
        return next((b for b in self.buses if b.bus_id == bus_id), None)

    def bus_names(self) -> dict[str, str]:
        """Bus id -> display name."""
        return {b.bus_id: b.name for b in self.buses}

    def required_sources(self) -> list[str]:
        """Sources mapped by enabled buses, in first-use order."""
        out: list[str] = []
        for bus in self.buses:
            if bus.enabled:
                out.extend(s for s in bus.role_map.values() if s and s not in out)
        return out

    def remap_sources(self, mapping: Mapping[str, str]) -> None:
        """Rename sources in every bus (role maps and thresholds).

        Args:
            mapping: Old source name -> new name; unlisted names are kept.
        """
        for bus in self.buses:
            bus.role_map = {role: mapping.get(src, src) for role, src in bus.role_map.items()}
            bus.thresholds = {mapping.get(src, src): th for src, th in bus.thresholds.items()}

    def device_bindings(self) -> tuple[list[DeviceBinding], dict[int, str]]:
        """Load every bound map (cached) and build mapper bindings.

        Returns:
            ``(bindings, errors)``: bindings for maps that loaded, and
            binding index -> error text for those that did not.
        """
        bindings: list[DeviceBinding] = []
        errors: dict[int, str] = {}
        for index, entry in enumerate(self.maps):
            try:
                device = _devices.load(entry.path) if entry.path else None
                bindings.append(DeviceBinding(entry.bus_id, device, entry.address, entry.layout()))
            except OSError as e:
                errors[index] = f"cannot read {Path(entry.path).name}: {e.strerror or e}"
            except Exception as e:  # noqa: BLE001 - shown next to the binding
                errors[index] = str(e) or repr(e)
        return bindings, errors

    def decode(self, waveforms: Mapping[str, Waveform], registry: DecoderRegistry | None = None) -> DecodeOutcome:
        """Decode every enabled bus and map register accesses.

        Args:
            waveforms: Source name -> ``(t, v)`` arrays or a logic signal.
            registry: Decoder registry (defaults to the shared one).

        Returns:
            Frames, command history and per-binding map errors.
        """
        registry = registry or decoder_registry()
        result = run(self.buses, waveforms, registry)
        bindings, errors = self.device_bindings()
        enabled = {b.bus_id for b in self.buses if b.enabled}
        bindings = [b for b in bindings if b.bus_id in enabled]
        accesses = annotate(result, bindings)
        names = self.bus_names()
        result.warnings.extend(
            f"{names.get(self.maps[i].bus_id, self.maps[i].bus_id)}: register map {self.maps[i].path!r}: {e}"
            for i, e in errors.items()
            if self.maps[i].bus_id in enabled
        )
        return DecodeOutcome(result, accesses, errors)

    def duplicate_bus(self, bus_id: str) -> BusConfig:
        """Copy a bus (and its map bindings) under a new id; returns the copy."""
        source = self.bus(bus_id)
        if source is None:
            raise KeyError(bus_id)
        clone = copy.deepcopy(source)
        clone.bus_id = new_bus_id()
        clone.name = f"{source.name} copy"
        self.buses.insert(self.buses.index(source) + 1, clone)
        self.maps.extend(replace(m, bus_id=clone.bus_id, spi_layout=dict(m.spi_layout)) for m in self.maps if m.bus_id == bus_id)
        return clone

    def remove_bus(self, bus_id: str) -> None:
        """Delete a bus and its map bindings."""
        self.buses = [b for b in self.buses if b.bus_id != bus_id]
        self.maps = [m for m in self.maps if m.bus_id != bus_id]
