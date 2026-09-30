"""Register field decoding tests."""

from better_scope.decode.regmap import Field, FieldValue, Register, decode_value

CFG = Register("VOUT_CFG", 0x1C, 8, fields=(
    Field("EN", 7, 1, enum={0: "Off", 1: "On"}),
    Field("MODE", 5, 2, enum={0: "Off", 1: "PWM", 2: "Auto"}),
    Field("VSEL", 0, 5),
))
STATUS = Register("STATUS", 0x10, 8, fields=(Field("FAULT", 7, 1), Field("PGOOD", 0, 1)))
VOUT = Register("VOUT", 0x21, 16, fields=(Field("RANGE", 14, 2, enum={2: "2-5 V"}), Field("CODE", 0, 12)))


def _texts(values: list[FieldValue]) -> list[str]:
    return [fv.text for fv in values]


def test_enum_labels_and_formats() -> None:
    assert _texts(decode_value(CFG, 0xCC)) == ["EN=On", "MODE=Auto", "VSEL=0x0C"]
    assert _texts(decode_value(CFG, 0x60)) == ["EN=Off", "MODE=0x3", "VSEL=0x00"]


def test_enum_label_on_one_bit_field() -> None:
    reg = Register("R", 0, 8, fields=(Field("EN", 0, 1, enum={1: "On"}),))
    assert decode_value(reg, 1)[0].text == "EN=On"


def test_reserved_bits_shown_only_when_set() -> None:
    assert _texts(decode_value(STATUS, 0x81)) == ["FAULT=1", "PGOOD=1"]
    values = decode_value(STATUS, 0x81 | 0x24)
    assert _texts(values) == ["FAULT=1", "RESERVED[6:1]=0x12", "PGOOD=1"]
    assert values[1].reserved and (values[1].msb, values[1].lsb) == (6, 1)


def test_single_reserved_bit_name() -> None:
    reg = Register("R", 0, 8, fields=(Field("A", 1, 7),))
    assert decode_value(reg, 0x01)[-1].name == "RESERVED[0]"


def test_16_bit_register() -> None:
    values = decode_value(VOUT, 0x9234)
    assert _texts(values) == ["RANGE=2-5 V", "RESERVED[13:12]=0x1", "CODE=0x234"]


def test_bits_above_width_are_reserved() -> None:
    assert _texts(decode_value(STATUS, 0x101)) == ["RESERVED[8]=1", "FAULT=0", "PGOOD=1"]


def test_register_without_fields_decodes_empty() -> None:
    assert decode_value(Register("ID", 0x30, 8), 0x5A) == []
