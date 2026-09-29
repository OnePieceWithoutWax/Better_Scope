"""Turn analog waveforms into :class:`LogicSignal` objects.

A vectorized Schmitt trigger: samples at or above ``threshold + h/2`` read
high, samples at or below ``threshold - h/2`` read low, and anything inside the
band keeps the previous level. No Python loop runs per sample.
"""

__lazy_modules__ = ["numpy"]

from typing import Literal

import numpy as np

from better_scope.decode.model import LogicSignal, Threshold

# Default hysteresis as a fraction of the low-to-high swing.
DEFAULT_HYSTERESIS_FRACTION = 0.1

# Below this many samples per bit/clock, edge timing is unreliable.
MIN_SAMPLES_PER_BIT = 4.0


def signal_levels(v: np.ndarray) -> tuple[float, float]:
    """Estimate the low and high logic levels of an analog trace.

    Uses the 5th/95th percentiles. When one level occupies more than ~95% of
    the record (a mostly idle UART line, say) those percentiles both land on
    the same level, so it falls back to the medians of the samples either side
    of the min/max midpoint.

    Args:
        v: Voltage samples.

    Returns:
        ``(low, high)`` in volts.
    """
    p5, p95 = (float(x) for x in np.percentile(v, [5, 95]))
    vmin, vmax = float(np.min(v)), float(np.max(v))
    full = vmax - vmin
    if full > 0 and (p95 - p5) < 0.5 * full:
        mid = (vmin + vmax) / 2
        return float(np.median(v[v < mid])), float(np.median(v[v >= mid]))
    return p5, p95


def digitize(
    t: np.ndarray,
    v: np.ndarray,
    threshold: float | Literal["auto"] = "auto",
    hysteresis: float | None = None,
) -> LogicSignal:
    """Digitize an analog waveform with a Schmitt trigger.

    Args:
        t: Sample times in seconds (assumed uniformly spaced).
        v: Voltage samples, same length as ``t``. A boolean array is taken
            as already-digital data and used as-is.
        threshold: ``"auto"`` (midpoint of the low/high levels) or a
            threshold in volts.
        hysteresis: Total band width in volts. ``None`` uses
            ``DEFAULT_HYSTERESIS_FRACTION`` of the swing.

    Returns:
        The digitized signal; ``warnings`` notes a flat trace.
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v)
    if t.size != v.size:
        raise ValueError(f"t and v differ in length ({t.size} vs {v.size})")
    if t.size < 2:
        raise ValueError("need at least 2 samples to digitize")
    dt = float(t[-1] - t[0]) / (t.size - 1)
    if v.dtype == bool:
        return LogicSignal.from_bool(v, dt, float(t[0]))

    v = v.astype(float, copy=False)
    low, high = signal_levels(v)
    swing = high - low
    warnings: list[str] = []
    if swing <= 0:
        warnings.append("flat trace: no logic swing found")
    thr = (low + high) / 2 if threshold == "auto" else float(threshold)
    hyst = DEFAULT_HYSTERESIS_FRACTION * swing if hysteresis is None else float(hysteresis)
    hi_level, lo_level = thr + hyst / 2, thr - hyst / 2

    state = np.full(v.size, -1, dtype=np.int8)
    state[v >= hi_level] = 1
    state[v <= lo_level] = 0
    if state[0] < 0:
        state[0] = 1 if v[0] >= thr else 0
    # Forward-fill undecided (in-band) samples with the last decided level.
    idx = np.where(state >= 0, np.arange(v.size), 0)
    np.maximum.accumulate(idx, out=idx)
    levels = state[idx].astype(bool)

    sig = LogicSignal.from_bool(levels, dt, float(t[0]))
    if warnings:
        sig = LogicSignal(sig.t0, sig.dt, sig.n_samples, sig.initial, sig.edges, tuple(warnings))
    return sig


def digitize_with(t: np.ndarray, v: np.ndarray, setting: Threshold) -> LogicSignal:
    """Digitize using a persisted :class:`Threshold` setting."""
    threshold: float | Literal["auto"] = "auto" if setting.mode == "auto" else setting.level
    return digitize(t, v, threshold, setting.hysteresis)


def oversampling_warning(signal: LogicSignal, rate_hz: float, what: str = "bit") -> str | None:
    """Return a warning if ``signal`` has too few samples per bit/clock.

    Args:
        signal: The digitized signal.
        rate_hz: Bit or clock rate in Hz.
        what: Word used in the message (``"bit"``, ``"clock"``).

    Returns:
        The warning text, or ``None`` if sampling is adequate.
    """
    spb = signal.samples_per(1.0 / rate_hz)
    if spb < MIN_SAMPLES_PER_BIT:
        return (
            f"only {spb:.1f} samples per {what} at {rate_hz:g} Hz "
            f"(need >= {MIN_SAMPLES_PER_BIT:g}); edges will be unreliable"
        )
    return None
