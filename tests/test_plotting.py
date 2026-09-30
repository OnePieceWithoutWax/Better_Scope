"""Tests for the GUI-agnostic plotting helpers."""

import numpy as np

from better_scope.decode.model import Frame
from better_scope.plotting import (
    FrameIndex,
    box_series,
    coalesce_spans,
    fit_label,
    minmax_decimate,
    visible_slice,
)


def _frame(start: float, end: float) -> Frame:
    return Frame(start, end, "b", "d", "byte", 1)


def test_decimate_short_record_unchanged() -> None:
    t = np.arange(10.0)
    x, y = minmax_decimate(t, t * 2, 100)
    assert x == t.tolist()
    assert y == (t * 2).tolist()


def test_decimate_keeps_narrow_glitch() -> None:
    n = 100_000
    t = np.arange(n) * 1e-9
    v = np.zeros(n)
    v[54_321] = 5.0
    v[77_777] = -3.0
    x, y = minmax_decimate(t, v, 500)
    assert len(x) <= 2 * 500 + 2
    assert max(y) == 5.0
    assert min(y) == -3.0
    assert x == sorted(x)


def test_decimate_includes_tail() -> None:
    n = 1003
    t = np.arange(n, dtype=float)
    v = np.zeros(n)
    v[-1] = 1.0
    x, y = minmax_decimate(t, v, 100)
    assert x[-1] == n - 1
    assert y[-1] == 1.0


def test_visible_slice_pads_one_sample() -> None:
    t = np.arange(100, dtype=float)
    s = visible_slice(t, 10.5, 20.5)
    assert (s.start, s.stop) == (10, 22)
    assert visible_slice(t, 200, 300).stop - visible_slice(t, 200, 300).start <= 1
    full = visible_slice(t, -5, 500)
    assert (full.start, full.stop) == (0, 100)


def test_frame_index_visible_range() -> None:
    frames = [_frame(i, i + 0.5) for i in range(10)]
    idx = FrameIndex(frames)
    got = idx.visible(3.2, 5.1)
    assert [f.start for f in got] == [3, 4, 5]
    assert idx.visible(20, 30) == []
    assert len(idx.visible(-10, 100)) == 10


def test_frame_index_long_overlapping_frame() -> None:
    frames = [_frame(0, 100), _frame(1, 2), _frame(50, 51)]
    got = FrameIndex(frames).visible(60, 70)
    assert [f.start for f in got] == [0]


def test_fit_label_picks_longest_that_fits() -> None:
    variants = ("WR VOUT_COMMAND = 0x0123", "WR VOUT_COMMAND", "VOUT")
    assert fit_label(variants, 1000) == variants[0]
    assert fit_label(variants, 120) == "WR VOUT_COMMAND"
    assert fit_label(variants, 40) == "VOUT"
    assert fit_label(variants, 10) is None
    assert fit_label((), 100) is None


def test_coalesce_spans() -> None:
    spans = [(0.0, 1.0), (1.05, 2.0), (5.0, 6.0), (6.0, 6.5)]
    assert coalesce_spans(spans, 0.1) == [(0.0, 2.0), (5.0, 6.5)]
    assert coalesce_spans(spans, 0.0) == spans
    assert coalesce_spans([], 1.0) == []


def test_box_series_shape() -> None:
    x, y1, y2 = box_series([(0.0, 1.0), (2.0, 2.01)], 0.1, 0.9, inset=0.02)
    assert x == [0.02, 0.02, 0.98, 0.98, 2.0, 2.0, 2.01, 2.01]
    assert y1 == [0.5, 0.1, 0.1, 0.5] * 2
    assert y2 == [0.5, 0.9, 0.9, 0.5] * 2


def test_decimate_bins_cover_whole_range() -> None:
    # 3270 samples into 1357 bins: every bin spans 2-3 samples, none is huge.
    n = 3270
    t = np.arange(n, dtype=float)
    v = np.sin(t / 10.0)
    x, _ = minmax_decimate(t, v, 1357)
    assert len(x) == 2 * 1357
    assert x[0] == 0 and x[-1] == n - 1
    assert max(np.diff(x)) <= 3
