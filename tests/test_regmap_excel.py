"""Excel register-map importer tests."""

import datetime
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from better_scope.decode.regmap import RegmapError, load_register_map
from better_scope.decode.regmap.excel import TEMPLATE_HEADERS, parse_bits, parse_enum, parse_int, write_template

TEMPLATE = Path(__file__).resolve().parents[1] / "resources" / "register_map_template.xlsx"


def _write(
    tmp_path: Path,
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str] = TEMPLATE_HEADERS,
    device: Sequence[tuple[str, Any]] | None = None,
    sheet: str = "Registers",
    title_rows: int = 0,
) -> Path:
    """Write a one-device workbook and return its path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for _ in range(title_rows):
        ws.append(("My device register map",))
    ws.append(tuple(headers))
    for row in rows:
        ws.append(tuple(row))
    if device is not None:
        ws_dev = wb.create_sheet("Device")
        for key, value in device:
            ws_dev.append((key, value))
    path = tmp_path / "map.xlsx"
    wb.save(path)
    return path


def _issues(path: Path) -> list[tuple[int | None, str | None, str]]:
    with pytest.raises(RegmapError) as info:
        load_register_map(path)
    return [(i.row, i.column, i.message) for i in info.value.issues]


# -- parsers -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["0x1A", "0X1a", "1Ah", "1ah", "26", "0b11010", 26, 26.0, " 0x1A "])
def test_parse_int_formats(value: Any) -> None:
    assert parse_int(value) == 26


@pytest.mark.parametrize("value", ["1A", "0xZZ", "", 1.5, -1, True])
def test_parse_int_rejects(value: Any) -> None:
    with pytest.raises(ValueError):
        parse_int(value)


@pytest.mark.parametrize("value", ["7:4", "[7:4]", "7..4", "4:7", " [7 : 4] ", datetime.time(7, 4)])
def test_parse_bits_ranges(value: Any) -> None:
    assert parse_bits(value) == (7, 4)


@pytest.mark.parametrize("value", ["3", "[3]", 3, 3.0])
def test_parse_bits_single(value: Any) -> None:
    assert parse_bits(value) == (3, 3)


@pytest.mark.parametrize("value", ["7-4", "a:b", "7:4:1", ""])
def test_parse_bits_rejects(value: Any) -> None:
    with pytest.raises(ValueError):
        parse_bits(value)


def test_parse_enum() -> None:
    assert parse_enum("0=Off; 1=On; 2=Auto") == {0: "Off", 1: "On", 2: "Auto"}
    assert parse_enum("0: Off\n0x1: On\n") == {0: "Off", 1: "On"}
    assert parse_enum("0=Off, 1=On") == {0: "Off", 1: "On"}
    for bad in ("Off; On", "0=Off; 0=On", "x=Off"):
        with pytest.raises(ValueError):
            parse_enum(bad)


# -- workbook ----------------------------------------------------------------


def test_template_round_trip(tmp_path: Path) -> None:
    committed = load_register_map(TEMPLATE)
    fresh = load_register_map(write_template(tmp_path / "t.xlsx"))
    assert committed.registers == fresh.registers
    assert (committed.name, committed.bus, committed.address) == ("EXAMPLE_PMIC", "smbus", 0x40)
    cfg = committed.by_name("vout_cfg")
    assert cfg is not None and cfg.address == 0x1C and cfg.reset == 0x0C
    assert [(f.name, f.msb, f.lsb) for f in cfg.fields] == [("EN", 7, 7), ("MODE", 6, 5), ("VSEL", 4, 0)]
    assert cfg.fields[1].enum == {0: "Off", 1: "PWM", 2: "Auto"}
    assert committed.registers[0x21].width == 16
    assert committed.registers[0x30].fields == ()


def test_forward_fill_and_defaults(tmp_path: Path) -> None:
    rows = [
        ("CTRL", "0x01", None, "rw", "0x80", None, None, "Control register", None),
        (None, None, None, None, None, "EN", "7", "Enable", "0=Off;1=On"),
        (None, None, None, None, None, "MODE", "1:0", None, None),
        ("STAT", "0x02", None, None, None, "OK", "0", None, None),
    ]
    device = load_register_map(_write(tmp_path, rows, device=[("addr_width", 8), ("data_width", 8)]))
    ctrl = device.registers[1]
    assert (ctrl.name, ctrl.width, ctrl.access, ctrl.reset, ctrl.description) == ("CTRL", 8, "RW", 0x80, "Control register")
    assert [f.name for f in ctrl.fields] == ["EN", "MODE"]
    assert ctrl.fields[0].description == "Enable"
    assert device.registers[2].fields[0].name == "OK"
    assert device.name == "map"  # file stem when the Device sheet has no name


def test_repeated_register_cells_are_one_register(tmp_path: Path) -> None:
    rows = [
        ("CTRL", "0x01", 8, "RW", None, "EN", "7", None, None),
        ("CTRL", "0x01", 8, "RW", None, "MODE", "1:0", None, None),
    ]
    device = load_register_map(_write(tmp_path, rows))
    assert [f.name for f in device.registers[1].fields] == ["EN", "MODE"]


def test_alias_headers_title_row_and_first_sheet(tmp_path: Path) -> None:
    headers = ["Reg Name", "Offset", "Size (bits)", "R/W", "Default", "Bit Field", "Bit Range", "Desc", "Values"]
    rows = [("VOUT", "21h", 16, "RW", "0", "RANGE", "15:14", "Range", "0=Low; 1=High"),
            (None, None, None, None, None, "CODE", "11:0", None, None)]
    device = load_register_map(_write(tmp_path, rows, headers=headers, sheet="Map", title_rows=2))
    vout = device.registers[0x21]
    assert vout.width == 16 and vout.fields[0].enum == {0: "Low", 1: "High"}


def test_msb_lsb_columns_and_numeric_cells(tmp_path: Path) -> None:
    headers = ["Register", "Address", "Field", "MSB", "LSB"]
    rows = [("CTRL", 1, "EN", 7, 7), (None, None, "MODE", 1, 0)]
    device = load_register_map(_write(tmp_path, rows, headers=headers))
    assert [(f.name, f.msb, f.lsb) for f in device.registers[1].fields] == [("EN", 7, 7), ("MODE", 1, 0)]


def test_excel_time_bits_cell(tmp_path: Path) -> None:
    rows = [("CTRL", "0x01", None, None, None, "MODE", datetime.time(7, 4), None, None)]
    field = load_register_map(_write(tmp_path, rows)).registers[1].fields[0]
    assert (field.msb, field.lsb) == (7, 4)


def test_device_sheet(tmp_path: Path) -> None:
    rows = [("CMD", "0xD0", None, None, None, None, None, None, None)]
    device = load_register_map(_write(tmp_path, rows, device=[
        ("Name", "PMIC_A"), ("Bus", "PMBus"), ("Address", "0x5A"), ("Data Width", 16),
    ]))
    assert (device.name, device.bus, device.address, device.data_width) == ("PMIC_A", "pmbus", 0x5A, 16)
    assert device.registers[0xD0].width == 16


# -- validation errors (row numbers count the header as row 1) ---------------


def test_overlapping_fields(tmp_path: Path) -> None:
    rows = [("CTRL", "0x01", 8, None, None, "A", "7:4", None, None),
            (None, None, None, None, None, "B", "5", None, None)]
    ((row, column, message),) = _issues(_write(tmp_path, rows))
    assert row == 3 and column == "G (Bits)" and "overlaps field A" in message


def test_field_outside_width(tmp_path: Path) -> None:
    rows = [("CTRL", "0x01", 8, None, None, "A", "9:8", None, None)]
    ((row, column, message),) = _issues(_write(tmp_path, rows))
    assert row == 2 and column == "G (Bits)" and "outside the 8-bit register" in message


def test_duplicate_address(tmp_path: Path) -> None:
    rows = [("A", "0x01", None, None, None, None, None, None, None),
            ("B", "1", None, None, None, None, None, None, None)]
    ((row, column, message),) = _issues(_write(tmp_path, rows))
    assert row == 3 and column == "B (Address)" and "duplicate address 0x1 (also register A)" in message


@pytest.mark.parametrize(
    ("cells", "column", "text"),
    [
        ({1: "0xZZ"}, "B (Address)", "cannot parse"),
        ({6: "7-4"}, "G (Bits)", "as bits"),
        ({4: "high"}, "E (Reset)", "cannot parse"),
        ({2: "wide"}, "C (Width)", "cannot parse"),
        ({8: "Off; On"}, "I (Enum)", "not 'value=label'"),
        ({8: "0=a; 4=b"}, "I (Enum)", "do not fit"),
    ],
)
def test_unparsable_values(tmp_path: Path, cells: dict[int, str], column: str, text: str) -> None:
    row = ["CTRL", "0x01", 8, "RW", "0", "MODE", "1:0", None, None]
    for index, value in cells.items():
        row[index] = value
    issues = _issues(_write(tmp_path, [row]))
    assert (2, column) in [(r, c) for r, c, _ in issues]
    assert any(text in m for _, _, m in issues)


def test_all_errors_reported_together(tmp_path: Path) -> None:
    rows = [("A", "0x01", 8, None, None, "X", "9", None, None),
            ("B", "0x01", None, None, None, None, None, None, None),
            ("C", "nope", None, None, None, None, None, None, None)]
    with pytest.raises(RegmapError) as info:
        load_register_map(_write(tmp_path, rows))
    assert [i.row for i in info.value.issues] == [2, 4]  # A's field; C's address (A dropped, so B is not a dup)
    assert "sheet 'Registers', row 2, column G (Bits)" in str(info.value)


def test_missing_header(tmp_path: Path) -> None:
    ((_, _, message),) = _issues(_write(tmp_path, [], headers=["Foo", "Bar"]))
    assert "no header row" in message


def test_field_row_before_register(tmp_path: Path) -> None:
    rows = [(None, None, None, None, None, "EN", "0", None, None)]
    ((row, column, message),) = _issues(_write(tmp_path, rows))
    assert (row, column) == (2, "A (Register)") and "before any register" in message


@pytest.mark.parametrize(
    ("key", "value", "text"),
    [("bus", "can", "bus must be one of"), ("address", "0x80", "not a 7-bit address"), ("data_width", 0, "at least 1")],
)
def test_device_sheet_errors(tmp_path: Path, key: str, value: Any, text: str) -> None:
    rows = [("A", "0x01", None, None, None, None, None, None, None)]
    ((row, column, message),) = _issues(_write(tmp_path, rows, device=[("name", "X"), (key, value)]))
    assert (row, column) == (2, "B") and text in message


def test_unknown_extension(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        load_register_map(tmp_path / "map.foo")


def test_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(RegmapError, match="cannot open workbook"):
        load_register_map(path)
