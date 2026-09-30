"""Tests for the decode session, command-history rows and events CSV."""

import csv
from pathlib import Path

from better_scope.decode import BusConfig, Level, Threshold
from better_scope.decode.events import event_rows, filter_events
from better_scope.decode.export import events_to_csv
from better_scope.decode.regmap.excel import write_template
from better_scope.decode.session import DecodeSession, MapBinding
from tests.signals import I2cSegment, i2c_waveform

S = I2cSegment


def _pmbus_waves() -> dict:
    # VOUT_MODE (0x20) write byte to 0x40, then a NACKed address to 0x41.
    scl, sda = i2c_waveform(
        [[S(0x40, data=[0x20, 0x17])], [S(0x41, address_ack=False)]],
        100e3,
        samples_per_bit=20,
    )
    return {"CH1": scl, "CH2": sda}


def _session() -> DecodeSession:
    bus = BusConfig("p", "PMBus", "pmbus", role_map={"scl": "CH1", "sda": "CH2"}, options={"pec": "off"})
    return DecodeSession([bus])


def test_session_dict_round_trip() -> None:
    bus = BusConfig("b", "Bus", "spi", role_map={"sclk": "CH1"}, thresholds={"CH1": Threshold("manual", 1.2, 0.1)})
    session = DecodeSession([bus], [MapBinding("b", "x.xlsx", 0x40, {"frame_bits": 24})])
    again = DecodeSession.from_dicts(session.bus_dicts(), session.map_dicts())
    assert again.bus_dicts() == session.bus_dicts()
    assert again.maps == session.maps
    assert again.maps[0].layout().frame_bits == 24


def test_session_decode_pmbus_history() -> None:
    outcome = _session().decode(_pmbus_waves())
    names = [a.register_name for a in outcome.accesses]
    assert names == ["VOUT_MODE", "?"]
    assert outcome.accesses[1].errors
    assert outcome.result.for_bus("p", Level.SEMANTIC)[0].decoder_id == "regmap"


def test_session_required_sources_and_remap() -> None:
    session = _session()
    session.buses.append(BusConfig("u", "UART", "uart", role_map={"rx": "CH3"}, enabled=False))
    assert session.required_sources() == ["CH1", "CH2"]
    session.buses[0].thresholds["CH1"] = Threshold("manual", 1.0)
    session.remap_sources({"CH1": "FILE:CH1", "CH2": "FILE:CH2"})
    assert session.buses[0].role_map == {"scl": "FILE:CH1", "sda": "FILE:CH2"}
    assert "FILE:CH1" in session.buses[0].thresholds


def test_session_duplicate_and_remove() -> None:
    session = _session()
    session.maps.append(MapBinding("p", "", 0x40))
    clone = session.duplicate_bus("p")
    assert clone.bus_id != "p" and session.buses[1] is clone
    assert [m.bus_id for m in session.maps] == ["p", clone.bus_id]
    session.remove_bus("p")
    assert [b.bus_id for b in session.buses] == [clone.bus_id]
    assert [m.bus_id for m in session.maps] == [clone.bus_id]


def test_session_bad_map_is_reported_not_fatal(tmp_path: Path) -> None:
    session = _session()
    session.maps.append(MapBinding("p", str(tmp_path / "missing.xlsx")))
    outcome = session.decode(_pmbus_waves())
    assert 0 in outcome.map_errors
    assert any("missing.xlsx" in w for w in outcome.result.warnings)


def test_session_loads_excel_map(tmp_path: Path) -> None:
    path = write_template(tmp_path / "map.xlsx")
    session = _session()
    session.maps.append(MapBinding("p", str(path), 0x40))
    bindings, errors = session.device_bindings()
    assert errors == {}
    assert bindings[0].device is not None and bindings[0].address == 0x40


def test_event_rows_filters_and_csv(tmp_path: Path) -> None:
    scl, sda = i2c_waveform(
        [[S(0x40, data=[0x20, 0x17])], [S(0x40, data=[0x21, 0x00, 0x10])], [S(0x40, data=[0x01, 0x80, 0x00])]],
        100e3,
        samples_per_bit=20,
    )
    session = _session()
    outcome = session.decode({"CH1": scl, "CH2": sda})
    rows = event_rows(outcome.accesses, session.bus_names())
    assert [r.register for r in rows] == ["VOUT_MODE", "VOUT_COMMAND", "OPERATION"]
    assert rows[0].bus == "PMBus" and rows[0].device == "PMBus (0x40)" and rows[0].rw == "W"
    assert rows[0].value == "0x17"
    # OPERATION is a write byte; the extra byte makes it a size mismatch error.
    assert rows[2].error
    assert [r.register for r in filter_events(rows, register_text="vout")] == ["VOUT_MODE", "VOUT_COMMAND"]
    assert filter_events(rows, errors_only=True) == [rows[2]]
    assert filter_events(rows, bus_id="other") == []
    assert filter_events(rows, device="PMBus (0x40)") == rows

    out = events_to_csv(rows, tmp_path / "events.csv")
    with out.open(encoding="utf-8") as f:
        table = list(csv.reader(f))
    assert table[0] == ["time", "bus", "device", "rw", "register", "value", "fields", "error"]
    assert float(table[1][0]) == rows[0].start
    assert table[2][4] == "VOUT_COMMAND"
