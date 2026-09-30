"""I2C decoder tests on synthetic analog waveforms."""

from collections.abc import Sequence

from better_scope.decode import BusConfig, DecodeResult, Level, run
from tests.signals import I2cSegment, i2c_waveform

FREQ = 100e3


def _decode(
    transactions: Sequence[Sequence[I2cSegment]], options: dict[str, object] | None = None, **kwargs: object
) -> DecodeResult:
    scl, sda = i2c_waveform(transactions, FREQ, **kwargs)  # type: ignore[arg-type]
    bus = BusConfig("b", "I2C", "i2c", role_map={"scl": "CH1", "sda": "CH2"}, options=options or {})
    return run([bus], {"CH1": scl, "CH2": sda})


def _kinds(result: DecodeResult, level: int) -> list[str]:
    return [f.kind for f in result.for_bus("b", level)]


def test_write() -> None:
    result = _decode([[I2cSegment(0x50, data=[0x10, 0xAB])]])
    words = result.for_bus("b", Level.WORD)
    assert [f.kind for f in words] == ["address", "data", "data"]
    assert words[0].data == {"address": 0x50, "read": False, "ack": True}
    assert [f.data["value"] for f in words[1:]] == [0x10, 0xAB]
    assert _kinds(result, Level.BIT).count("ack") == 3
    (txn,) = result.for_bus("b", Level.PACKET)
    assert txn.data["complete"] is True
    assert txn.data["segments"][0]["data"] == [0x10, 0xAB]
    assert result.errors == []
    assert result.warnings == []


def test_read_with_repeated_start() -> None:
    result = _decode([[I2cSegment(0x50, data=[0x10]), I2cSegment(0x50, read=True, data=[0xDE, 0xAD, 0xBE])]])
    bits = _kinds(result, Level.BIT)
    assert bits.count("start") == 1 and bits.count("repeated_start") == 1 and bits.count("stop") == 1
    (txn,) = result.for_bus("b", Level.PACKET)
    segs = txn.data["segments"]
    assert [(s["address"], s["read"], s["data"]) for s in segs] == [(0x50, False, [0x10]), (0x50, True, [0xDE, 0xAD, 0xBE])]
    # The controller NACKs the last read byte; that is not an error.
    assert segs[1]["acks"] == [True, True, False]
    assert result.errors == []
    assert txn.long_text == "W 0x50: 10 | R 0x50: DE AD BE"


def test_address_nack() -> None:
    result = _decode([[I2cSegment(0x21, address_ack=False)], [I2cSegment(0x22, data=[0x01])]])
    words = result.for_bus("b", Level.WORD)
    assert words[0].kind == "address" and words[0].error == "address NACK"
    txns = result.for_bus("b", Level.PACKET)
    assert "NACK" in (txns[0].error or "")
    assert txns[1].error is None and txns[1].data["segments"][0]["data"] == [0x01]


def test_written_byte_nack_is_an_error() -> None:
    result = _decode([[I2cSegment(0x22, data=[0x01, 0x02], nack_data=[1])]])
    data = [f for f in result.for_bus("b", Level.WORD) if f.kind == "data"]
    assert [f.error for f in data] == [None, "data NACK"]


def test_clock_stretching() -> None:
    txns = [[I2cSegment(0x50, data=[0x10]), I2cSegment(0x50, read=True, data=[0x12, 0x34])]]
    result = _decode(txns, stretch_bits=7.5)
    (txn,) = result.for_bus("b", Level.PACKET)
    assert txn.data["segments"][1]["data"] == [0x12, 0x34]
    assert txn.data["max_scl_low"] > 7.5 / FREQ
    assert result.errors == []


def test_noise_and_slow_edges() -> None:
    txns = [[I2cSegment(0x3C, data=[0x00, 0xFF, 0x55, 0xAA])]]
    result = _decode(txns, noise=0.06, edge_fraction=0.15, samples_per_bit=16)
    data = [f.data["value"] for f in result.for_bus("b", Level.WORD) if f.kind == "data"]
    assert data == [0x00, 0xFF, 0x55, 0xAA]


def test_8bit_address_format() -> None:
    result = _decode([[I2cSegment(0x50, read=True, data=[0x01])]], options={"address_format": "8-bit"})
    addr = result.for_bus("b", Level.WORD)[0]
    assert addr.data["address"] == 0x50
    assert "0xA1" in addr.long_text


def test_ten_bit_address_flagged_not_decoded() -> None:
    result = _decode([[I2cSegment(0x2A5, ten_bit=True, data=[0x01, 0x02])], [I2cSegment(0x10, data=[0x07])]])
    words = result.for_bus("b", Level.WORD)
    assert words[0].kind == "ten_bit_address" and words[0].error
    txns = result.for_bus("b", Level.PACKET)
    assert "10-bit" in (txns[0].error or "")
    assert txns[0].data["segments"][0]["data"] == []
    # Decoding resumes at the next START.
    assert txns[1].data["segments"][0]["address"] == 0x10 and txns[1].error is None


def test_capture_ends_mid_transaction() -> None:
    (t, scl), (_, sda) = i2c_waveform([[I2cSegment(0x50, data=[0x10, 0x20, 0x30])]], FREQ)
    cut = int((4 + 0.25 + 9 * 2 + 4.5) * 40)  # idle, START, two bytes, half of the third
    bus = BusConfig("b", "I2C", "i2c", role_map={"scl": "CH1", "sda": "CH2"})
    result = run([bus], {"CH1": (t[:cut], scl[:cut]), "CH2": (t[:cut], sda[:cut])})
    (txn,) = result.for_bus("b", Level.PACKET)
    assert txn.data["complete"] is False
    assert "capture ended mid-transaction" in (txn.error or "")
    assert any(f.kind == "incomplete_byte" for f in result.for_bus("b", Level.WORD))


def test_capture_starts_mid_transaction() -> None:
    (t, scl), (_, sda) = i2c_waveform([[I2cSegment(0x50, data=[0x10, 0x20])], [I2cSegment(0x51, data=[0x30])]], FREQ)
    cut = int((4 + 0.25 + 9 * 1.5) * 40)
    bus = BusConfig("b", "I2C", "i2c", role_map={"scl": "CH1", "sda": "CH2"})
    result = run([bus], {"CH1": (t[cut:], scl[cut:]), "CH2": (t[cut:], sda[cut:])})
    (txn,) = result.for_bus("b", Level.PACKET)
    assert txn.data["segments"][0]["address"] == 0x51
    assert any("before the first START" in w for w in result.warnings)
