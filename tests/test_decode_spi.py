"""SPI decoder tests on synthetic analog waveforms."""

import pytest

from better_scope.decode import BusConfig, DecodeResult, Level, run
from tests.signals import spi_waveform

FREQ = 1e6
TXNS = [[(0xA5, 0x3C), (0x01, 0xFE), (0x80, 0x7F)], [(0x55, 0xAA)]]
ROLES = {"sclk": "CH1", "mosi": "CH2", "miso": "CH3", "cs": "CH4"}


def _decode(waves: dict[str, tuple], roles: dict[str, str] = ROLES, **options: object) -> DecodeResult:
    sources = {"CH1": waves["sclk"], "CH2": waves["mosi"], "CH3": waves["miso"], "CH4": waves["cs"]}
    bus = BusConfig("s", "SPI", "spi", role_map=roles, options=options)
    return run([bus], sources)


def _pairs(result: DecodeResult) -> list[list[tuple[int, int]]]:
    return [list(zip(t.data["mosi"], t.data["miso"])) for t in result.for_bus("s", Level.PACKET)]


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_all_modes(mode: int) -> None:
    result = _decode(spi_waveform(TXNS, FREQ, mode=mode), mode=mode)
    assert _pairs(result) == TXNS
    assert result.errors == []


def test_lsb_first() -> None:
    result = _decode(spi_waveform(TXNS, FREQ, lsb_first=True), bit_order="lsb")
    assert _pairs(result) == TXNS


def test_16_bit_words() -> None:
    txns = [[(0xBEEF, 0x1234), (0x0001, 0x8000)]]
    result = _decode(spi_waveform(txns, FREQ, word_size=16), word_size=16)
    assert _pairs(result) == txns
    assert result.for_bus("s", Level.WORD)[0].long_text == "MOSI 0xBEEF / MISO 0x1234"


def test_cs_active_high() -> None:
    result = _decode(spi_waveform(TXNS, FREQ, cs_active_low=False), cs_polarity="high")
    assert _pairs(result) == TXNS


def test_no_cs_idle_gap_split() -> None:
    roles = {k: v for k, v in ROLES.items() if k != "cs"}
    result = _decode(spi_waveform(TXNS, FREQ, gap_clocks=30), roles=roles)
    assert _pairs(result) == TXNS


def test_mosi_only() -> None:
    roles = {"sclk": "CH1", "mosi": "CH2", "cs": "CH4"}
    result = _decode(spi_waveform(TXNS, FREQ), roles=roles)
    txn = result.for_bus("s", Level.PACKET)[0]
    assert txn.data["mosi"] == [0xA5, 0x01, 0x80] and txn.data["miso"] is None


def test_truncated_word() -> None:
    result = _decode(spi_waveform(TXNS, FREQ, partial_bits=3))
    last = result.for_bus("s", Level.PACKET)[-1]
    assert last.data["mosi"] == [0x55]
    assert last.error == "incomplete word (3 of 8 bits)"
    assert any(f.kind == "incomplete_word" for f in result.for_bus("s", Level.WORD))


def test_capture_ends_with_cs_asserted() -> None:
    waves = spi_waveform([[(0x12, 0x34), (0x56, 0x78)]], FREQ)
    cut = int((20 + 0.5 + 8 + 4) * 20)  # gap, CS setup, one word, half the next
    result = _decode({k: (t[:cut], v[:cut]) for k, (t, v) in waves.items()})
    (txn,) = result.for_bus("s", Level.PACKET)
    assert txn.data["ended_after_capture"] is True
    assert txn.data["mosi"] == [0x12]
    assert "incomplete word" in (txn.error or "")


def test_invalid_word_size_is_a_warning() -> None:
    result = _decode(spi_waveform(TXNS, FREQ), word_size=40)
    assert result.frames == {}
    assert any("word size" in w for w in result.warnings)
