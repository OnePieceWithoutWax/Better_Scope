"""Tests for saving and loading waveform files."""

from pathlib import Path

import numpy as np
import pytest

from better_scope.decode import BusConfig
from better_scope.decode.session import DecodeSession, MapBinding
from better_scope.waveform_io import load_waveforms, save_waveforms, time_base
from tests.signals import I2cSegment, i2c_waveform

S = I2cSegment

# Synthetic stand-in for a Tektronix 4/5/6 Series CSV (no real sample file has
# been supplied yet). It only exercises what the loader relies on: a block of
# "key,value[,value...]" header rows, blank and comma-only rows, a TIME column
# row in mixed case, trailing empty columns, and multi-channel data.
TEK_CSV = """\
Model,MSO58
Firmware Version,2.0.3

Waveform Type,ANALOG,ANALOG
Point Format,Y,Y
Horizontal Units,s,s
Sample Interval,1e-06,1e-06
Record Length,5,5
Vertical Units,V,A
Label,SDA,IOUT
,,
Time,CH1,CH2,
-2.000000e-06,0.01,1.5,
-1.000000e-06,3.29,1.6,
0.000000e+00,3.31,1.7,
1.000000e-06,0.02,1.8,
2.000000e-06,0.00,1.9,
"""


def _pmbus_waves() -> dict:
    scl, sda = i2c_waveform([[S(0x40, data=[0x20, 0x17])], [S(0x40, data=[0x21, 0x00, 0x10])]], 100e3, samples_per_bit=20)
    return {"CH1": scl, "CH2": sda}


def _session() -> DecodeSession:
    bus = BusConfig("p", "PMBus", "pmbus", role_map={"scl": "CH1", "sda": "CH2"}, options={"pec": "off"})
    return DecodeSession([bus], [MapBinding("p", "", 0x40)])


def test_native_round_trip_with_bus_configs(tmp_path: Path) -> None:
    waves = _pmbus_waves()
    session = _session()
    meta = {"scope": "MSO58 (SN: X)", "bus_configs": session.bus_dicts(), "map_bindings": session.map_dicts()}
    path = save_waveforms(tmp_path / "cap.npz", waves, meta)

    loaded, got = load_waveforms(path)
    assert got["sources"] == ["CH1", "CH2"]
    assert got["scope"] == "MSO58 (SN: X)"
    assert got["units"] == {"CH1": "V", "CH2": "V"}
    for src, (t, v) in waves.items():
        lt, lv = loaded[src]
        assert lt.size == t.size
        assert np.allclose(lt, t, rtol=0, atol=1e-15)
        assert np.allclose(lv, v, atol=1e-5)

    again = DecodeSession.from_dicts(got["bus_configs"], got["map_bindings"])
    assert again.bus_dicts() == session.bus_dicts()
    assert again.maps == session.maps


def test_decoding_loaded_file_matches_original(tmp_path: Path) -> None:
    waves = _pmbus_waves()
    session = _session()
    original = session.decode(waves)
    for name in ("cap.npz", "cap.csv"):
        loaded, _ = load_waveforms(save_waveforms(tmp_path / name, waves))
        again = session.decode(loaded)
        assert [a.labels for a in again.accesses] == [a.labels for a in original.accesses]
        frames_a = [(f.kind, f.long_text) for f in original.result.iter_frames()]
        frames_b = [(f.kind, f.long_text) for f in again.result.iter_frames()]
        assert frames_a == frames_b
    assert len(original.accesses) == 2


def test_tek_csv(tmp_path: Path) -> None:
    path = tmp_path / "tek.csv"
    path.write_text(TEK_CSV, encoding="utf-8")
    waves, meta = load_waveforms(path)
    assert meta["format"] == "tek_csv"
    assert meta["sources"] == ["CH1", "CH2"]
    assert meta["header"]["Model"] == "MSO58"
    assert meta["header"]["Label"] == ["SDA", "IOUT"]
    assert meta["units"] == {"CH1": "V", "CH2": "A"}
    t, v = waves["CH1"]
    assert t.tolist() == [-2e-6, -1e-6, 0.0, 1e-6, 2e-6]
    assert v.tolist() == [0.01, 3.29, 3.31, 0.02, 0.0]
    assert waves["CH2"][1][-1] == 1.9
    assert time_base(t) == pytest.approx((-2e-6, 1e-6))


def test_generic_csv_with_and_without_header(tmp_path: Path) -> None:
    named = tmp_path / "named.csv"
    named.write_text("Time (s),Voltage (V)\n0,1.0\n1e-6,2.0\n2e-6,3.0\n", encoding="utf-8")
    waves, meta = load_waveforms(named)
    assert meta["format"] == "csv"
    assert list(waves) == ["Voltage (V)"]
    assert waves["Voltage (V)"][1].tolist() == [1.0, 2.0, 3.0]

    bare = tmp_path / "bare.csv"
    bare.write_text("0,1,5\n1,2,6\n", encoding="utf-8")
    waves, meta = load_waveforms(bare)
    assert list(waves) == ["COL1", "COL2"]
    assert waves["COL2"][1].tolist() == [5.0, 6.0]


def test_csv_save_needs_common_time_base(tmp_path: Path) -> None:
    t = np.arange(10) * 1e-6
    with pytest.raises(ValueError, match="same time base"):
        save_waveforms(tmp_path / "x.csv", {"CH1": (t, t), "CH2": (t[:5], t[:5])})


def test_bad_files(tmp_path: Path) -> None:
    text = tmp_path / "notes.csv"
    text.write_text("hello,world\nno,numbers\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no numeric"):
        load_waveforms(text)
    with pytest.raises(ValueError, match=r"\.wfm"):
        load_waveforms(tmp_path / "x.wfm")
    with pytest.raises(ValueError):
        save_waveforms(tmp_path / "x.bin", {"CH1": (np.arange(3.0), np.arange(3.0))})
    other = tmp_path / "other.npz"
    np.savez(other, a=np.arange(3))
    with pytest.raises(ValueError, match="not a Better_Scope"):
        load_waveforms(other)
