"""Run bus configurations over captured waveforms.

:func:`run` digitizes each source once per threshold setting, runs each bus's
base decoder, then its stacked layers bottom-up. Problems with one bus
(unknown decoder, unmapped role, bad option, decoder exception) become
warnings on the result; the other buses still decode.
"""

__lazy_modules__ = ["numpy"]

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from better_scope.decode.api import Decoder, DecodeError
from better_scope.decode.digitize import digitize_with
from better_scope.decode.model import BusConfig, DecodeResult, Frame, LogicSignal
from better_scope.decode.registry import DecoderRegistry, decoder_registry

logger = logging.getLogger(__name__)

# A source is either raw ``(t, v)`` arrays or an already-digital signal.
Waveform = tuple[np.ndarray, np.ndarray] | LogicSignal


def resolve_options(chain: list[type[Decoder]], given: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge user options over every layer's defaults and coerce types.

    Args:
        chain: Decoder classes of the stack, root first.
        given: User-supplied option values.

    Returns:
        ``(options, warnings)``. Invalid or unknown values produce a warning
        and fall back to the default.
    """
    options: dict[str, Any] = {}
    warnings: list[str] = []
    declared = {opt.id: opt for cls in chain for opt in cls.options}
    for opt_id, opt in declared.items():
        options[opt_id] = opt.default
        if opt_id in given:
            try:
                options[opt_id] = opt.coerce(given[opt_id])
            except ValueError as e:
                warnings.append(f"{e}; using default {opt.default!r}")
    for opt_id in given:
        if opt_id not in declared:
            warnings.append(f"unknown option {opt_id!r} ignored")
    return options, warnings


class _SignalCache:
    """Digitizes each (source, threshold) pair at most once per run."""

    def __init__(self, waveforms: Mapping[str, Waveform], result: DecodeResult) -> None:
        self.waveforms = waveforms
        self.result = result
        self._cache: dict[tuple[str, Any], LogicSignal] = {}

    def get(self, source: str, bus: BusConfig) -> LogicSignal:
        wave = self.waveforms[source]
        if isinstance(wave, LogicSignal):
            return wave
        setting = bus.threshold_for(source)
        key = (source, setting.key())
        if key not in self._cache:
            t, v = wave
            sig = digitize_with(t, v, setting)
            self._cache[key] = sig
            self.result.warnings.extend(f"{source}: {w}" for w in sig.warnings)
        return self._cache[key]


def run(
    bus_configs: Iterable[BusConfig],
    waveforms: Mapping[str, Waveform],
    registry: DecoderRegistry | None = None,
) -> DecodeResult:
    """Decode every enabled bus.

    Args:
        bus_configs: Buses to decode.
        waveforms: Source name -> ``(t, v)`` arrays (as returned by
            ``BetterScope.acquire_waveforms``) or a :class:`LogicSignal`.
        registry: Decoder registry (defaults to the shared one).

    Returns:
        Frames of every bus, sorted, plus warnings.
    """
    registry = registry or decoder_registry()
    result = DecodeResult()
    cache = _SignalCache(waveforms, result)
    for bus in bus_configs:
        if not bus.enabled:
            continue
        try:
            frames = _run_bus(bus, cache, registry, result)
        except DecodeError as e:
            result.warnings.append(f"{bus.name}: {e}")
            continue
        except Exception as e:
            logger.exception(f"decoder crashed on bus {bus.name!r}")
            result.warnings.append(f"{bus.name}: decoder error: {e!r}")
            continue
        result.extend(frames)
    result.sort()
    return result


def _run_bus(bus: BusConfig, cache: _SignalCache, registry: DecoderRegistry, result: DecodeResult) -> list[Frame]:
    """Decode one bus through its whole stack; returns every layer's frames."""
    try:
        chain = registry.chain(bus.decoder_id)
    except KeyError as e:
        raise DecodeError(f"unknown decoder {e.args[0]!r}") from None
    root = chain[0]

    for role in root.roles:
        if role.required and role.id not in bus.role_map:
            raise DecodeError(f"required role {role.label!r} is not mapped")
    if root.require_one_of and not any(r in bus.role_map for r in root.require_one_of):
        raise DecodeError(f"map at least one of {list(root.require_one_of)}")
    role_ids = {r.id for r in root.roles}
    signals: dict[str, LogicSignal] = {}
    for role_id, source in bus.role_map.items():
        if role_id not in role_ids:
            result.warnings.append(f"{bus.name}: unknown role {role_id!r} ignored")
            continue
        if source not in cache.waveforms:
            raise DecodeError(f"source {source!r} for role {role_id!r} has no waveform")
        signals[role_id] = cache.get(source, bus)

    opts, opt_warnings = resolve_options(chain, bus.options)
    result.warnings.extend(f"{bus.name}: {w}" for w in opt_warnings)

    all_frames: list[Frame] = []
    below: list[Frame] = []
    for index, cls in enumerate(chain):
        decoder = cls(bus_id=bus.bus_id)
        if index == 0:
            layer = list(decoder.decode(signals, dict(opts)))
        else:
            layer = list(decoder.decode_frames(below, dict(opts)))
        result.warnings.extend(f"{bus.name} [{cls.id}]: {w}" for w in decoder.warnings)
        layer.sort(key=lambda f: f.start)
        all_frames.extend(layer)
        below = layer
    return all_frames
