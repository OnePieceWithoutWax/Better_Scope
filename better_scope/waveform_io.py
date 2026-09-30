"""Save acquired waveforms to disk and read saved ones back.

Every loader returns the same shape as ``BetterScope.acquire_waveforms``:
``{source: (time_array, voltage_array)}`` plus a ``meta`` dict, so a loaded
file plots and decodes exactly like a live acquisition. GUI-agnostic.

Formats (chosen by extension; CSV flavours are sniffed):

- **Native** ``.npz``: per source the time base ``[t0, dt]`` (float64) and
  the samples (float32), plus a ``meta`` JSON string (format tag, app
  version, scope id, capture time, units, source order and, optionally, the
  bus configs and register-map bindings needed to repeat the decode).
- **Tektronix CSV**: a block of ``key,value[,value...]`` header rows, then a
  ``TIME,CH1,...`` column row (matched case-insensitively), then numbers.
  The header rows go into ``meta["header"]`` as-is; nothing else about their
  layout is assumed.
- **Generic CSV**: a time column then one column per source, with an
  optional column-name row.

Tektronix ``.wfm`` is not supported yet (needs a sample file to verify
against Tek's reference waveform file-format document).
"""

__lazy_modules__ = ["numpy"]

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from better_scope.version import __version__

NATIVE_FORMAT = "better_scope.waveforms"
NATIVE_VERSION = 1

# Give up looking for numeric data after this many header rows.
_MAX_HEADER_ROWS = 1000

Waveforms = dict[str, tuple[np.ndarray, np.ndarray]]


def time_base(t: np.ndarray) -> tuple[float, float]:
    """``(t0, dt)`` of a uniformly sampled time array.

    Args:
        t: Sample times (at least 2 samples).

    Returns:
        First sample time and mean sample interval.
    """
    t = np.asarray(t, dtype=float)
    if t.size < 2:
        raise ValueError("a waveform needs at least 2 samples")
    return float(t[0]), float(t[-1] - t[0]) / (t.size - 1)


def save_waveforms(path: Path, waveforms: Mapping[str, tuple[Any, Any]], meta: Mapping[str, Any] | None = None) -> Path:
    """Write waveforms to ``.npz`` (native) or ``.csv`` (generic).

    Args:
        path: Destination; the extension picks the format. Parent folders
            are created.
        waveforms: Source name -> ``(t, v)`` arrays.
        meta: Extra metadata for the native format (``scope``,
            ``capture_time``, ``units``, ``bus_configs``, ``map_bindings``...).
            Ignored for CSV.

    Returns:
        The path written.

    Raises:
        ValueError: For an unsupported extension, an empty waveform set, or
            CSV sources that do not share one time base.
    """
    path = Path(path)
    if not waveforms:
        raise ValueError("no waveforms to save")
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".npz":
        _save_npz(path, waveforms, dict(meta or {}))
    elif suffix == ".csv":
        _save_csv(path, waveforms)
    else:
        raise ValueError(f"cannot save waveforms as {suffix or 'a file without extension'!r} (use .npz or .csv)")
    return path


def load_waveforms(path: Path) -> tuple[Waveforms, dict[str, Any]]:
    """Read a waveform file.

    Args:
        path: ``.npz`` (native), ``.csv``/``.txt`` (Tektronix or generic).

    Returns:
        ``(waveforms, meta)``; ``meta["format"]`` names the detected format
        and ``meta["sources"]`` lists the sources in file order.

    Raises:
        ValueError: If the file is not a recognised waveform file.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".npz":
        return _load_npz(path)
    if suffix in (".csv", ".txt"):
        return _load_csv(path)
    if suffix == ".wfm":
        raise ValueError(
            "Tektronix .wfm files are not supported yet; save the waveform as CSV on the scope, "
            "or send a sample .wfm so a reader can be written and verified"
        )
    raise ValueError(f"unknown waveform file type {suffix!r}")


# -- native ------------------------------------------------------------------


def _save_npz(path: Path, waveforms: Mapping[str, tuple[Any, Any]], meta: dict[str, Any]) -> None:
    sources = list(waveforms)
    meta.update(
        format=NATIVE_FORMAT,
        version=NATIVE_VERSION,
        app_version=__version__,
        saved_at=datetime.now().isoformat(timespec="seconds"),
        sources=sources,
    )
    meta.setdefault("units", {s: "V" for s in sources})
    arrays: dict[str, np.ndarray] = {"meta": np.array(json.dumps(meta, default=str))}
    for i, source in enumerate(sources):
        t, v = waveforms[source]
        t0, dt = time_base(t)
        arrays[f"s{i}_timebase"] = np.array([t0, dt], dtype=np.float64)
        arrays[f"s{i}_samples"] = np.asarray(v, dtype=np.float32)
    # np.savez appends ".npz" when missing; the caller's suffix is already .npz.
    np.savez(path, **arrays)


def _load_npz(path: Path) -> tuple[Waveforms, dict[str, Any]]:
    with np.load(path, allow_pickle=False) as data:
        if "meta" not in data.files:
            raise ValueError(f"{path.name} is not a Better_Scope waveform file (no meta)")
        meta = json.loads(str(data["meta"]))
        if meta.get("format") != NATIVE_FORMAT:
            raise ValueError(f"{path.name}: unknown format {meta.get('format')!r}")
        waveforms: Waveforms = {}
        for i, source in enumerate(meta["sources"]):
            t0, dt = (float(x) for x in data[f"s{i}_timebase"])
            v = data[f"s{i}_samples"].astype(np.float64)
            waveforms[source] = (t0 + np.arange(v.size) * dt, v)
    return waveforms, meta


# -- CSV ---------------------------------------------------------------------


def _save_csv(path: Path, waveforms: Mapping[str, tuple[Any, Any]]) -> None:
    sources = list(waveforms)
    t_ref = np.asarray(waveforms[sources[0]][0], dtype=float)
    columns = [t_ref]
    for source in sources:
        t, v = waveforms[source]
        t = np.asarray(t, dtype=float)
        if t.size != t_ref.size or not np.allclose(time_base(t), time_base(t_ref), rtol=1e-9, atol=0.0):
            raise ValueError("CSV needs every source on the same time base; save as .npz instead")
        columns.append(np.asarray(v, dtype=float))
    np.savetxt(
        path,
        np.column_stack(columns),
        delimiter=",",
        header=",".join(["TIME", *sources]),
        comments="",
        fmt=["%.12e"] + ["%.7e"] * len(sources),
    )


def _is_number(cell: str) -> bool:
    try:
        float(cell)
    except ValueError:
        return False
    return True


def _is_numeric_row(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    return bool(cells) and bool(cells[0]) and all(_is_number(c) for c in filled)


def _load_csv(path: Path) -> tuple[Waveforms, dict[str, Any]]:
    header_rows: list[list[str]] = []
    column_row: list[str] | None = None
    tek = False
    data_start: int | None = None
    first_data: list[str] = []
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for index, line in enumerate(f):
            cells = [c.strip() for c in line.rstrip("\r\n").split(",")]
            if _is_numeric_row(cells):
                data_start, first_data = index, cells
                break
            if index >= _MAX_HEADER_ROWS:
                break
            if cells[0].upper() == "TIME":
                column_row, tek = cells, True
            elif not tek and any(cells):
                header_rows.append(cells)
    if data_start is None:
        raise ValueError(f"{path.name}: no numeric waveform data found")

    if column_row is None and header_rows and data_start > 0:
        # Generic CSV: the last text row before the numbers names the columns.
        column_row = header_rows.pop()

    n_cols = len(first_data)
    while n_cols > 1 and not first_data[n_cols - 1]:
        n_cols -= 1
    if n_cols < 2:
        raise ValueError(f"{path.name}: needs a time column and at least one source column")

    names = list(column_row[1:n_cols]) if column_row else []
    if len(names) != n_cols - 1 or len(set(names)) != len(names) or not all(names):
        names = [f"COL{i}" for i in range(1, n_cols)]

    data = np.loadtxt(
        path,
        delimiter=",",
        skiprows=data_start,
        usecols=range(n_cols),
        ndmin=2,
        encoding="utf-8-sig",
    )
    t = data[:, 0].copy()
    waveforms: Waveforms = {name: (t, data[:, i + 1].copy()) for i, name in enumerate(names)}

    header = {row[0]: _header_value(row[1:]) for row in header_rows if row and row[0]}
    meta: dict[str, Any] = {
        "format": "tek_csv" if tek else "csv",
        "sources": names,
        "header": header,
        "units": _units(header, names),
    }
    return waveforms, meta


def _header_value(cells: list[str]) -> str | list[str]:
    values = [c for c in cells if c]
    if len(values) == 1:
        return values[0]
    return values if values else ""


def _units(header: Mapping[str, Any], names: list[str]) -> dict[str, str]:
    """Per-source units from a ``Vertical Units`` header row, else volts."""
    units = next((v for k, v in header.items() if k.strip().lower() == "vertical units"), "V")
    if isinstance(units, list):
        return {n: units[i] if i < len(units) else "V" for i, n in enumerate(names)}
    return {n: units or "V" for n in names}
