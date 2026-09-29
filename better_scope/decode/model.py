"""Data model for the software serial decoder.

Everything here is GUI-agnostic and JSON-friendly where it needs to persist:

- :class:`LogicSignal` -- a digitized trace stored as its transitions only.
- :class:`Frame` -- one decoded item (bit, byte, packet, register access).
- :class:`DecodeResult` -- frames grouped by bus and level, plus warnings.
- :class:`Threshold` / :class:`BusConfig` -- the persisted bus configuration.
"""

__lazy_modules__ = ["numpy"]

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Literal, Self

import numpy as np

EdgeKind = Literal["rising", "falling", "any"]

# Guards float round-off when a time lands exactly on a sample.
_EPS = 1e-9


class Level(IntEnum):
    """Annotation level of a frame, lowest (bits) to highest (semantic)."""

    BIT = 0
    WORD = 1
    PACKET = 2
    SEMANTIC = 3


@dataclass(frozen=True, eq=False)
class LogicSignal:
    """A digital trace on a uniform time base, stored as transitions.

    Sample ``i`` is at time ``t0 + i * dt``. ``edges[k]`` is the index of the
    first sample *after* the k-th transition, so the level is ``initial`` for
    samples before ``edges[0]`` and flips at every edge. Decoders walk edges
    instead of samples, which keeps long records fast.

    Attributes:
        t0: Time of sample 0 in seconds.
        dt: Sample interval in seconds.
        n_samples: Number of samples in the original record.
        initial: Level of sample 0.
        edges: Sorted int64 array of transition sample indices.
        warnings: Notes from digitizing (e.g. a flat trace).
    """

    t0: float
    dt: float
    n_samples: int
    initial: bool
    edges: np.ndarray
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_bool(cls, levels: Iterable[bool] | np.ndarray, dt: float, t0: float = 0.0) -> Self:
        """Build a signal from a boolean sample array (e.g. a digital probe).

        Args:
            levels: One boolean per sample.
            dt: Sample interval in seconds.
            t0: Time of the first sample in seconds.

        Returns:
            The equivalent :class:`LogicSignal`.
        """
        arr = np.asarray(levels, dtype=bool)
        if arr.size < 2:
            raise ValueError("a LogicSignal needs at least 2 samples")
        edges = np.flatnonzero(arr[1:] != arr[:-1]).astype(np.int64) + 1
        return cls(float(t0), float(dt), int(arr.size), bool(arr[0]), edges)

    @property
    def t_start(self) -> float:
        """Time of the first sample."""
        return self.t0

    @property
    def t_end(self) -> float:
        """Time of the last sample."""
        return self.t0 + (self.n_samples - 1) * self.dt

    @property
    def edge_times(self) -> np.ndarray:
        """Times of all transitions in seconds."""
        return self.t0 + self.edges * self.dt

    def inverted(self) -> Self:
        """Return the logically inverted signal (same edges, flipped levels)."""
        return type(self)(self.t0, self.dt, self.n_samples, not self.initial, self.edges, self.warnings)

    def to_bool(self) -> np.ndarray:
        """Expand back to one boolean per sample."""
        flips = np.zeros(self.n_samples, dtype=np.int8)
        flips[self.edges] = 1
        return (np.cumsum(flips) % 2).astype(bool) ^ self.initial

    def edge_is_rising(self, k: int) -> bool:
        """Whether edge ``k`` goes low-to-high."""
        # After an even-numbered edge the level is the inverse of ``initial``.
        return (not self.initial) if k % 2 == 0 else self.initial

    def _position(self, t: float) -> float:
        return (t - self.t0) / self.dt

    def level_at(self, t: float) -> bool:
        """Logic level at time ``t`` (the sample at or before ``t``)."""
        k = int(np.searchsorted(self.edges, np.floor(self._position(t) + _EPS), side="right"))
        return bool(self.initial ^ (k % 2))

    def levels_at(self, times: np.ndarray) -> np.ndarray:
        """Vectorized :meth:`level_at` for an array of times."""
        pos = np.floor((np.asarray(times, dtype=float) - self.t0) / self.dt + _EPS)
        k = np.searchsorted(self.edges, pos, side="right")
        return (k % 2).astype(bool) ^ self.initial

    def next_edge(self, t: float, kind: EdgeKind = "any") -> float | None:
        """Time of the first edge strictly after ``t``.

        Args:
            t: Search start time in seconds.
            kind: ``"rising"``, ``"falling"`` or ``"any"``.

        Returns:
            The edge time, or ``None`` if there is no such edge.
        """
        k = int(np.searchsorted(self.edges, self._position(t), side="right"))
        if kind != "any" and k < self.edges.size and self.edge_is_rising(k) != (kind == "rising"):
            k += 1
        if k >= self.edges.size:
            return None
        return self.t0 + float(self.edges[k]) * self.dt

    def edges_between(self, t_a: float, t_b: float, kind: EdgeKind = "any") -> np.ndarray:
        """Times of edges with ``t_a <= time < t_b``.

        Args:
            t_a: Window start in seconds (inclusive).
            t_b: Window end in seconds (exclusive).
            kind: ``"rising"``, ``"falling"`` or ``"any"``.

        Returns:
            Edge times in seconds.
        """
        lo = int(np.searchsorted(self.edges, self._position(t_a), side="left"))
        hi = int(np.searchsorted(self.edges, self._position(t_b), side="left"))
        idx = np.arange(lo, hi)
        if kind != "any":
            rising_even = not self.initial
            is_rising = (idx % 2 == 0) == rising_even
            idx = idx[is_rising == (kind == "rising")]
        return self.t0 + self.edges[idx] * self.dt

    def samples_per(self, period: float) -> float:
        """How many samples fall in ``period`` seconds (e.g. one bit time)."""
        return period / self.dt


@dataclass(eq=False)
class Frame:
    """One decoded item on a bus.

    Attributes:
        start: Start time in seconds.
        end: End time in seconds.
        bus_id: Bus this frame belongs to.
        decoder_id: Decoder that produced it.
        kind: Short type tag, e.g. ``"start"``, ``"byte"``, ``"parity_error"``.
        level: :class:`Level` (0=bit, 1=word, 2=packet, 3=semantic).
        data: Structured payload (JSON-serializable values).
        text: Label variants, longest first; the GUI picks one that fits.
        error: Error description, or ``None`` for a clean frame.
    """

    start: float
    end: float
    bus_id: str
    decoder_id: str
    kind: str
    level: int
    data: dict[str, Any] = field(default_factory=dict)
    text: tuple[str, ...] = ()
    error: str | None = None

    @property
    def long_text(self) -> str:
        """The longest label variant (empty if none)."""
        return self.text[0] if self.text else ""

    @property
    def short_text(self) -> str:
        """The shortest label variant (empty if none)."""
        return self.text[-1] if self.text else ""


@dataclass
class DecodeResult:
    """Frames grouped by bus and level, plus non-fatal warnings."""

    frames: dict[str, dict[int, list[Frame]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, frame: Frame) -> None:
        """Append a frame to its bus/level bucket."""
        self.frames.setdefault(frame.bus_id, {}).setdefault(int(frame.level), []).append(frame)

    def extend(self, frames: Iterable[Frame]) -> None:
        """Append several frames."""
        for frame in frames:
            self.add(frame)

    def sort(self) -> None:
        """Sort every bucket by start time (stable, so emit order breaks ties)."""
        for levels in self.frames.values():
            for bucket in levels.values():
                bucket.sort(key=lambda f: f.start)

    @property
    def bus_ids(self) -> list[str]:
        """Buses that produced at least one frame."""
        return list(self.frames)

    def for_bus(self, bus_id: str, level: int | None = None) -> list[Frame]:
        """Frames of one bus, optionally one level only, sorted by start."""
        levels = self.frames.get(bus_id, {})
        if level is not None:
            return list(levels.get(int(level), []))
        return sorted((f for bucket in levels.values() for f in bucket), key=lambda f: f.start)

    def iter_frames(
        self, levels: Iterable[int] | None = None, bus_ids: Iterable[str] | None = None
    ) -> Iterator[Frame]:
        """All frames matching the filters, sorted by start time then level.

        Args:
            levels: Levels to include (all if ``None``).
            bus_ids: Buses to include (all if ``None``).
        """
        wanted_levels = None if levels is None else {int(lv) for lv in levels}
        wanted_buses = None if bus_ids is None else set(bus_ids)
        selected = [
            f
            for bus, by_level in self.frames.items()
            if wanted_buses is None or bus in wanted_buses
            for lv, bucket in by_level.items()
            if wanted_levels is None or lv in wanted_levels
            for f in bucket
        ]
        selected.sort(key=lambda f: (f.start, f.level))
        return iter(selected)

    @property
    def errors(self) -> list[Frame]:
        """Every frame carrying an error, sorted by start time."""
        return [f for f in self.iter_frames() if f.error]


@dataclass
class Threshold:
    """Digitizing threshold for one source.

    Attributes:
        mode: ``"auto"`` (midpoint of the signal's low/high levels) or
            ``"manual"`` (use ``level``).
        level: Manual threshold in volts.
        hysteresis: Total hysteresis band in volts; ``None`` picks a small
            fraction of the signal swing.
    """

    mode: Literal["auto", "manual"] = "auto"
    level: float = 0.0
    hysteresis: float | None = None

    def key(self) -> tuple[str, float, float | None]:
        """Hashable identity used to cache digitized signals."""
        return (self.mode, self.level if self.mode == "manual" else 0.0, self.hysteresis)


@dataclass
class BusConfig:
    """Persisted configuration of one decoded bus.

    Attributes:
        bus_id: Stable unique id (used to tag frames).
        name: Display name.
        decoder_id: Top decoder of the stack (e.g. ``"pmbus"``); the engine
            runs every layer it stacks on.
        role_map: Root-decoder role id -> source name (``{"rx": "CH2"}``).
        thresholds: Source name -> :class:`Threshold` (auto if missing).
        options: Option id -> value, shared by all layers of the stack.
        enabled: Disabled buses are skipped by the engine.
    """

    bus_id: str
    name: str
    decoder_id: str
    role_map: dict[str, str] = field(default_factory=dict)
    thresholds: dict[str, Threshold] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def threshold_for(self, source: str) -> Threshold:
        """Threshold settings for ``source`` (auto by default)."""
        return self.thresholds.get(source, Threshold())

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            bus_id=data["bus_id"],
            name=data.get("name", data["bus_id"]),
            decoder_id=data["decoder_id"],
            role_map=dict(data.get("role_map", {})),
            thresholds={src: Threshold(**th) for src, th in data.get("thresholds", {}).items()},
            options=dict(data.get("options", {})),
            enabled=bool(data.get("enabled", True)),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Deserialize from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))
