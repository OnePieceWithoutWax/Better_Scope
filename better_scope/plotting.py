"""GUI-agnostic helpers for drawing long waveforms and decode frames.

Nothing here imports DearPyGui, so it is unit-testable and reusable from a
notebook: min/max decimation of a record down to a pixel budget, selecting
the frames inside a visible time window, and picking the label variant that
fits a box.
"""

__lazy_modules__ = ["numpy"]

import bisect
from collections.abc import Sequence

import numpy as np

from better_scope.decode.model import Frame


def visible_slice(t: np.ndarray, x_min: float, x_max: float) -> slice:
    """Index range of a sorted time array covering ``[x_min, x_max]``.

    One sample either side is included so lines run to the plot edge.

    Args:
        t: Sorted sample times.
        x_min: Window start.
        x_max: Window end.

    Returns:
        A slice into ``t`` (possibly empty).
    """
    lo = max(int(np.searchsorted(t, x_min, side="left")) - 1, 0)
    hi = min(int(np.searchsorted(t, x_max, side="right")) + 1, t.size)
    return slice(lo, max(lo, hi))


def minmax_decimate(t: np.ndarray, v: np.ndarray, n_bins: int) -> tuple[list[float], list[float]]:
    """Reduce a record to at most ``2 * n_bins`` points, keeping every peak.

    The record is split into ``n_bins`` runs of (near-)equal sample count;
    each run becomes its minimum at the run's first time and its maximum at
    its last time, so glitches narrower than a pixel stay visible. Short
    records are returned unchanged.

    Args:
        t: Sample times.
        v: Sample values (same length as ``t``).
        n_bins: Target bin count, typically the plot's pixel width.

    Returns:
        ``(x, y)`` as Python lists, ready for a DearPyGui series.
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    n = t.size
    n_bins = max(int(n_bins), 1)
    if n <= 2 * n_bins:
        return t.tolist(), v.tolist()

    edges = np.linspace(0, n, n_bins + 1).astype(np.int64)
    starts = edges[:-1]
    x = np.empty(2 * n_bins)
    y = np.empty(2 * n_bins)
    x[0::2] = t[starts]
    x[1::2] = t[edges[1:] - 1]
    y[0::2] = np.minimum.reduceat(v, starts)
    y[1::2] = np.maximum.reduceat(v, starts)
    return x.tolist(), y.tolist()


class FrameIndex:
    """Fast lookup of the frames that overlap a time window.

    Frames must be sorted by start time (as :class:`DecodeResult` keeps
    them). A running maximum of the end times makes the lower bound exact
    even when frames overlap.
    """

    def __init__(self, frames: Sequence[Frame]) -> None:
        """Index ``frames`` (sorted by start)."""
        self.frames = list(frames)
        self._starts = [f.start for f in self.frames]
        self._max_ends: list[float] = []
        running = float("-inf")
        for f in self.frames:
            running = max(running, f.end)
            self._max_ends.append(running)

    def __len__(self) -> int:
        return len(self.frames)

    def visible(self, x_min: float, x_max: float) -> list[Frame]:
        """Frames with ``end >= x_min`` and ``start <= x_max``, in start order."""
        lo = bisect.bisect_left(self._max_ends, x_min)
        hi = bisect.bisect_right(self._starts, x_max)
        return [f for f in self.frames[lo:hi] if f.end >= x_min]


def fit_label(variants: Sequence[str], width_px: float, char_px: float = 7.0, pad_px: float = 6.0) -> str | None:
    """The longest label variant that fits in a box.

    Args:
        variants: Label variants, longest first (``Frame.text``).
        width_px: Box width in pixels.
        char_px: Approximate width of one character in pixels.
        pad_px: Horizontal padding to leave free.

    Returns:
        The label, or ``None`` if not even the shortest variant fits.
    """
    room = width_px - pad_px
    for text in variants:
        if text and len(text) * char_px <= room:
            return text
    return None


def coalesce_spans(spans: Sequence[tuple[float, float]], min_gap: float) -> list[tuple[float, float]]:
    """Merge spans separated by less than ``min_gap``.

    Used when a zoomed-out lane holds more frames than it has pixels: boxes
    closer than a pixel are drawn as one.

    Args:
        spans: ``(start, end)`` pairs sorted by start.
        min_gap: Gaps narrower than this are closed.

    Returns:
        The merged spans, in order.
    """
    out: list[tuple[float, float]] = []
    for start, end in spans:
        if out and start - out[-1][1] < min_gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def box_series(
    spans: Sequence[tuple[float, float]], lo: float, hi: float, inset: float = 0.0
) -> tuple[list[float], list[float], list[float]]:
    """Outline many boxes as one shade series (``x``, ``y1``, ``y2``).

    Each box is filled between ``lo`` and ``hi``; between boxes the band
    collapses to zero height at the midline, so one plot item draws them all.

    Args:
        spans: ``(start, end)`` pairs sorted by start.
        lo: Box bottom (plot y units).
        hi: Box top.
        inset: Shrink each box by this much at both ends (x units) so
            adjacent boxes stay visibly separate.

    Returns:
        ``(x, y1, y2)`` lists for ``add_shade_series``.
    """
    mid = (lo + hi) / 2
    xs: list[float] = []
    y1: list[float] = []
    y2: list[float] = []
    for start, end in spans:
        a = start + inset if end - start > 2 * inset else start
        b = end - inset if end - start > 2 * inset else end
        xs += [a, a, b, b]
        y1 += [mid, lo, lo, mid]
        y2 += [mid, hi, hi, mid]
    return xs, y1, y2
