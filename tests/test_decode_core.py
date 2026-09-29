"""Tests for the decode model, digitizer, engine and CSV export."""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from better_scope.decode import BusConfig, Level, LogicSignal, Threshold, digitize, frames_to_csv, run
from better_scope.decode.api import Option
from tests.signals import levels_waveform, uart_waveform


def test_logic_signal_from_bool_helpers() -> None:
    levels = np.array([1, 1, 0, 0, 0, 1, 1, 0], dtype=bool)
    sig = LogicSignal.from_bool(levels, dt=1.0)
    assert sig.initial is True
    assert sig.edges.tolist() == [2, 5, 7]
    assert np.array_equal(sig.to_bool(), levels)
    assert sig.level_at(1.0) is True and sig.level_at(2.0) is False and sig.level_at(5.0) is True
    assert sig.next_edge(0.0) == 2.0
    assert sig.next_edge(2.0) == 5.0
    assert sig.next_edge(0.0, "rising") == 5.0
    assert sig.next_edge(5.0, "falling") == 7.0
    assert sig.next_edge(7.0) is None
    assert sig.edges_between(0.0, 7.0).tolist() == [2.0, 5.0]
    assert sig.edges_between(0.0, 8.0, "falling").tolist() == [2.0, 7.0]
    assert sig.inverted().level_at(0.0) is False


def test_digitize_hysteresis_rejects_noise() -> None:
    segs = [(False, 1e-3), (True, 1e-3), (False, 1e-3)]
    t, v = levels_waveform(segs, 1e6, noise=0.08, edge_time=20e-6, seed=3)
    sig = digitize(t, v)
    assert sig.edges.size == 2
    assert sig.edge_times[0] == pytest.approx(1e-3, abs=20e-6)


def test_digitize_manual_threshold_and_bool_input() -> None:
    t = np.arange(6) * 1e-6
    sig = digitize(t, np.array([0.0, 0.4, 0.6, 1.0, 0.6, 0.0]), threshold=0.5, hysteresis=0.0)
    assert sig.edges.tolist() == [2, 5]
    digital = digitize(t, np.array([0, 1, 1, 0, 0, 1], dtype=bool))
    assert digital.edges.tolist() == [1, 3, 5]


def test_digitize_flat_trace_warns() -> None:
    sig = digitize(np.arange(10.0), np.full(10, 1.2))
    assert sig.edges.size == 0
    assert sig.warnings


def test_option_coercion() -> None:
    opt = Option("stop_bits", "Stop", float, 1.0, choices=(1.0, 1.5, 2.0))
    assert opt.coerce("1.5") == 1.5
    assert opt.coerce(2) == 2.0
    with pytest.raises(ValueError):
        opt.coerce(3)
    flag = Option("invert", "Invert", bool, False)
    assert flag.coerce("on") is True
    with pytest.raises(ValueError):
        flag.coerce("maybe")


def test_bus_config_json_round_trip() -> None:
    bus = BusConfig(
        bus_id="b1",
        name="Debug UART",
        decoder_id="uart",
        role_map={"rx": "CH2", "tx": "CH3"},
        thresholds={"CH2": Threshold("manual", 1.65, 0.2), "CH3": Threshold()},
        options={"baud": 9600, "parity": "even", "stop_bits": 1.5, "invert": True},
        enabled=False,
    )
    restored = BusConfig.from_json(bus.to_json())
    assert restored == bus
    assert json.loads(bus.to_json())["thresholds"]["CH2"] == {"mode": "manual", "level": 1.65, "hysteresis": 0.2}


def test_engine_bad_option_and_unknown_decoder_are_warnings() -> None:
    wave = uart_waveform([0x42], 9600, idle_bits=12)
    good = BusConfig("a", "A", "uart", role_map={"rx": "CH1"}, options={"baud": 9600, "parity": "bogus"})
    unknown = BusConfig("b", "B", "no_such_decoder", role_map={"rx": "CH1"})
    missing = BusConfig("c", "C", "uart", role_map={"rx": "CH9"})
    result = run([good, unknown, missing], {"CH1": wave})
    assert [f.data["value"] for f in result.for_bus("a", Level.WORD)] == [0x42]
    text = "\n".join(result.warnings)
    assert "'bogus' is not one of" in text
    assert "unknown decoder 'no_such_decoder'" in text
    assert "'CH9'" in text


def test_engine_digitizes_each_source_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import better_scope.decode.engine as engine

    calls: list[str] = []
    real = engine.digitize_with

    def counting(t: np.ndarray, v: np.ndarray, setting: Threshold) -> LogicSignal:
        calls.append("x")
        return real(t, v, setting)

    monkeypatch.setattr(engine, "digitize_with", counting)
    wave = uart_waveform([0x42], 9600, idle_bits=12)
    buses = [BusConfig(f"b{i}", f"B{i}", "uart", role_map={"rx": "CH1"}, options={"baud": 9600}) for i in range(3)]
    run(buses, {"CH1": wave})
    assert len(calls) == 1


def test_frames_to_csv(tmp_path: Path) -> None:
    wave = uart_waveform([0x41, 0x42], 9600, gap_bits=1, idle_bits=12)
    bus = BusConfig("u", "UART", "uart", role_map={"rx": "CH1"}, options={"baud": 9600})
    result = run([bus], {"CH1": wave})
    out = frames_to_csv(result, tmp_path / "sub" / "frames.csv", levels=[Level.WORD])
    with out.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [json.loads(r["data"])["value"] for r in rows] == [0x41, 0x42]
    assert rows[0]["bus"] == "u" and rows[0]["decoder"] == "uart" and rows[0]["level"] == "1"
    assert float(rows[0]["time_start"]) < float(rows[1]["time_start"])
    assert rows[0]["error"] == ""
