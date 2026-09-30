"""SMBus decoder tests: protocol classification, PEC modes and timeouts."""

from collections.abc import Sequence
from dataclasses import replace

import pytest

from better_scope.decode import BusConfig, DecodeResult, Frame, Level, run
from better_scope.decode.decoders.smbus import crc8
from tests.signals import I2cSegment, i2c_waveform

FREQ = 100e3
ADDR = 0x40
S = I2cSegment


def _decode(
    transactions: Sequence[Sequence[I2cSegment]], decoder: str = "smbus", **options: object
) -> DecodeResult:
    scl, sda = i2c_waveform(transactions, FREQ, samples_per_bit=20)
    bus = BusConfig("b", "SMBus", decoder, role_map={"scl": "CH1", "sda": "CH2"}, options=options)
    return run([bus], {"CH1": scl, "CH2": sda})


def _smbus_txns(result: DecodeResult) -> list[Frame]:
    return [f for f in result.for_bus("b", Level.PACKET) if f.decoder_id == "smbus"]


def with_pec(segments: Sequence[I2cSegment], corrupt: bool = False) -> list[I2cSegment]:
    """Append the PEC of the whole message to the last segment."""
    message: list[int] = []
    for seg in segments:
        message += [(seg.address << 1) | int(seg.read), *seg.data]
    pec = crc8(message) ^ (0x01 if corrupt else 0)
    return [*segments[:-1], replace(segments[-1], data=[*segments[-1].data, pec])]


# (segments, type, command, data)
CASES: list[tuple[list[I2cSegment], str, int | None, list[int]]] = [
    ([S(ADDR, data=[0x03])], "send_byte", None, [0x03]),
    ([S(ADDR, read=True, data=[0x5A])], "receive_byte", None, [0x5A]),
    ([S(ADDR, data=[0x01, 0x80])], "write_byte", 0x01, [0x80]),
    ([S(ADDR, data=[0x21, 0x00, 0x1A])], "write_word", 0x21, [0x00, 0x1A]),
    ([S(ADDR, data=[0x50, 1, 2, 3, 4])], "write_32", 0x50, [1, 2, 3, 4]),
    ([S(ADDR, data=[0x51, *range(8)])], "write_64", 0x51, list(range(8))),
    ([S(ADDR, data=[0x19]), S(ADDR, read=True, data=[0xB0])], "read_byte", 0x19, [0xB0]),
    ([S(ADDR, data=[0x8B]), S(ADDR, read=True, data=[0x34, 0x12])], "read_word", 0x8B, [0x34, 0x12]),
    ([S(ADDR, data=[0x83]), S(ADDR, read=True, data=[9, 8, 7, 6])], "read_32", 0x83, [9, 8, 7, 6]),
    ([S(ADDR, data=[0x84]), S(ADDR, read=True, data=list(range(10, 18)))], "read_64", 0x84, list(range(10, 18))),
    ([S(ADDR, data=[0x99, 5, 0x41, 0x42, 0x43, 0x44, 0x45])], "block_write", 0x99, [0x41, 0x42, 0x43, 0x44, 0x45]),
    ([S(ADDR, data=[0x9A]), S(ADDR, read=True, data=[4, 1, 2, 3, 4])], "block_read", 0x9A, [1, 2, 3, 4]),
    ([S(ADDR, data=[0x30, 0x12, 0x34]), S(ADDR, read=True, data=[0x56, 0x78])], "process_call", 0x30, [0x56, 0x78]),
    (
        [S(ADDR, data=[0x06, 2, 0xAA, 0xBB]), S(ADDR, read=True, data=[3, 7, 8, 9])],
        "block_process_call", 0x06, [7, 8, 9],
    ),
]


def test_crc8_check_value() -> None:
    # CRC-8/SMBUS catalogue check value.
    assert crc8(b"123456789") == 0xF4


def test_quick_command() -> None:
    result = _decode([[S(ADDR)], [S(ADDR, read=True)]], pec="off")
    txns = _smbus_txns(result)
    assert [(f.data["type"], f.data["read"]) for f in txns] == [("quick_command", False), ("quick_command", True)]


@pytest.mark.parametrize(("segments", "kind", "command", "data"), CASES, ids=[c[1] for c in CASES])
def test_transaction_types_without_pec(segments: list[I2cSegment], kind: str, command: int | None, data: list[int]) -> None:
    (txn,) = _smbus_txns(_decode([segments], pec="off"))
    assert (txn.data["type"], txn.data["command"], txn.data["data"]) == (kind, command, data)
    assert txn.data["pec"] is None
    assert txn.error is None


@pytest.mark.parametrize(("segments", "kind", "command", "data"), CASES, ids=[c[1] for c in CASES])
def test_transaction_types_with_pec(segments: list[I2cSegment], kind: str, command: int | None, data: list[int]) -> None:
    result = _decode([with_pec(segments)], pec="on")
    (txn,) = _smbus_txns(result)
    assert (txn.data["type"], txn.data["command"], txn.data["data"]) == (kind, command, data)
    assert txn.data["pec_ok"] is True
    assert txn.error is None
    assert any(f.kind == "pec" for f in result.for_bus("b", Level.WORD))


def test_process_call_write_data() -> None:
    (txn,) = _smbus_txns(_decode([CASES[12][0]], pec="off"))
    assert txn.data["write_data"] == [0x12, 0x34] and txn.data["read_data"] == [0x56, 0x78]


def test_bad_pec() -> None:
    result = _decode([with_pec([S(ADDR, data=[0x21, 0x00, 0x1A])], corrupt=True)], pec="on")
    (txn,) = _smbus_txns(result)
    assert txn.data["type"] == "write_word"
    assert txn.data["pec_ok"] is False
    assert "PEC mismatch" in (txn.error or "")
    pec_frames = [f for f in result.for_bus("b", Level.WORD) if f.kind == "pec_error"]
    assert len(pec_frames) == 1 and pec_frames[0].error


def test_pec_auto_without_hints_guesses() -> None:
    plain = [S(ADDR, data=[0x8B]), S(ADDR, read=True, data=[0x34, 0x12])]
    result = _decode([with_pec(plain), plain], pec="auto")
    guessed, without = _smbus_txns(result)
    assert guessed.data["type"] == "read_word" and guessed.data["pec_ok"] is True
    assert guessed.data["pec_guessed"] is True
    assert "PEC?" in guessed.long_text
    assert without.data["type"] == "read_word" and without.data["pec"] is None


def test_address_nack_flagged() -> None:
    (txn,) = _smbus_txns(_decode([[S(0x33, address_ack=False)]]))
    assert txn.data["type"] == "address_nack"
    assert "NACK" in (txn.error or "")
    assert "NACK" in txn.long_text


def test_timeout_check() -> None:
    txns = [[S(ADDR, data=[0x01, 0x02])]]
    scl, sda = i2c_waveform(txns, FREQ, samples_per_bit=10, stretch_bits=3000)  # 30 ms clock-low
    bus = BusConfig("b", "SMBus", "smbus", role_map={"scl": "CH1", "sda": "CH2"})
    off = run([bus], {"CH1": scl, "CH2": sda})
    assert _smbus_txns(off)[0].error is None
    bus.options["smbus_timeout"] = True
    on = run([bus], {"CH1": scl, "CH2": sda})
    assert "tTIMEOUT" in (_smbus_txns(on)[0].error or "")


# -- PEC auto with PMBus size hints ------------------------------------------


def test_pmbus_hints_place_pec() -> None:
    write_word = [S(ADDR, data=[0x21, 0x00, 0x1A])]
    result = _decode([with_pec(write_word), write_word], decoder="pmbus", pec="auto")
    with_p, without = _smbus_txns(result)
    assert with_p.data["type"] == "write_word" and with_p.data["pec_ok"] is True
    assert with_p.data["pec_guessed"] is False
    assert without.data["type"] == "write_word" and without.data["pec"] is None
    assert result.errors == []


def test_pmbus_hints_detect_bad_pec() -> None:
    result = _decode([with_pec([S(ADDR, data=[0x21, 0x00, 0x1A])], corrupt=True)], decoder="pmbus", pec="auto")
    (txn,) = _smbus_txns(result)
    assert txn.data["type"] == "write_word"
    assert txn.data["pec_ok"] is False and "PEC mismatch" in (txn.error or "")


def test_pmbus_hints_resolve_send_byte_with_pec() -> None:
    # CLEAR_FAULTS + PEC is two bytes, the same shape as a Write Byte without PEC.
    result = _decode([with_pec([S(ADDR, data=[0x03])])], decoder="pmbus", pec="auto")
    (txn,) = _smbus_txns(result)
    assert txn.data["type"] == "send_byte" and txn.data["pec_ok"] is True


def test_pmbus_hints_block_read_with_pec() -> None:
    block = [S(ADDR, data=[0x9A]), S(ADDR, read=True, data=[3, 0x41, 0x42, 0x43])]
    (txn,) = _smbus_txns(_decode([with_pec(block)], decoder="pmbus", pec="auto"))
    assert txn.data["type"] == "block_read" and txn.data["data"] == [0x41, 0x42, 0x43]
    assert txn.data["pec_ok"] is True and txn.data["pec_guessed"] is False
