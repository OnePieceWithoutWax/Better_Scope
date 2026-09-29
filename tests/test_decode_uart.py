"""UART decoder tests on synthetic analog waveforms."""

import numpy as np
import pytest

from better_scope.decode import BusConfig, DecodeResult, Level, Threshold, digitize, run
from better_scope.decode.decoders.uart import estimate_baud
from tests.signals import Break, uart_waveform

BAUD = 115200
PAYLOAD = [0x55, 0x00, 0xFF, 0x41, 0x0D, 0xA5]


def _decode(wave: tuple[np.ndarray, np.ndarray], **options: object) -> DecodeResult:
    bus = BusConfig("u", "UART", "uart", role_map={"rx": "CH1"}, options={"baud": BAUD, **options})
    return run([bus], {"CH1": wave})


def _bytes(result: DecodeResult) -> list[int]:
    return [f.data["value"] for f in result.for_bus("u", Level.WORD) if f.kind == "byte"]


@pytest.mark.parametrize("data_bits", [5, 7, 8, 9])
@pytest.mark.parametrize("parity", ["none", "even", "odd", "mark", "space"])
@pytest.mark.parametrize("stop_bits", [1.0, 1.5, 2.0])
def test_round_trip_frame_formats(data_bits: int, parity: str, stop_bits: float) -> None:
    mask = (1 << data_bits) - 1
    values = [v & mask for v in PAYLOAD] + [mask]
    wave = uart_waveform(values, BAUD, data_bits=data_bits, parity=parity, stop_bits=stop_bits, gap_bits=1)
    result = _decode(wave, data_bits=data_bits, parity=parity, stop_bits=stop_bits)
    assert _bytes(result) == values
    assert result.errors == []


def test_back_to_back_characters_no_gap() -> None:
    result = _decode(uart_waveform(list(range(0, 256, 7)), BAUD))
    assert _bytes(result) == list(range(0, 256, 7))


def test_msb_first() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, lsb_first=False)
    assert _bytes(_decode(wave, bit_order="msb")) == PAYLOAD


def test_inverted_rs232_levels() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, invert=True, noise=0.03)
    bus = BusConfig(
        "u", "RS232", "uart", role_map={"rx": "CH1"},
        thresholds={"CH1": Threshold("manual", 0.0, 1.0)}, options={"baud": BAUD, "invert": True},
    )
    result = run([bus], {"CH1": wave})
    assert _bytes(result) == PAYLOAD


def test_noise_offset_and_slow_edges() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, noise=0.06, offset=0.4, edge_fraction=0.3, samples_per_bit=8)
    assert _bytes(_decode(wave)) == PAYLOAD


def test_mostly_idle_line_auto_threshold() -> None:
    wave = uart_waveform([0x5A], BAUD, idle_bits=400)
    result = _decode(wave)
    assert _bytes(result) == [0x5A]
    assert "uncertain" not in result.for_bus("u", Level.WORD)[0].data


def test_parity_error() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, parity="even", parity_errors=[3], gap_bits=2)
    result = _decode(wave, parity="even")
    assert _bytes(result) == PAYLOAD
    byte_frames = [f for f in result.for_bus("u", Level.WORD) if f.kind == "byte"]
    assert [f.error for f in byte_frames].count("parity error") == 1
    assert byte_frames[3].error == "parity error"
    assert any(f.kind == "parity_error" for f in result.for_bus("u", Level.BIT))


def test_framing_error() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, framing_errors=[2], gap_bits=2)
    result = _decode(wave)
    byte_frames = [f for f in result.for_bus("u", Level.WORD) if f.kind == "byte"]
    assert [f.data["value"] for f in byte_frames] == PAYLOAD
    assert byte_frames[2].error == "framing error"
    assert sum(1 for f in byte_frames if f.error) == 1


def test_break_detected_and_decoding_resumes() -> None:
    wave = uart_waveform([0x31, Break(25), 0x32], BAUD, gap_bits=3)
    result = _decode(wave)
    words = result.for_bus("u", Level.WORD)
    assert [f.kind for f in words] == ["byte", "break", "byte"]
    assert _bytes(result) == [0x31, 0x32]
    assert "uncertain" not in words[2].data
    brk = words[1]
    assert brk.end - brk.start == pytest.approx(25 / BAUD, rel=0.05)


def test_capture_starts_mid_byte() -> None:
    t, v = uart_waveform(PAYLOAD, BAUD, gap_bits=12, idle_bits=0.0)
    cut = int(4.3 * 16)  # four bits into the first character
    result = _decode((t[cut:], v[cut:]))
    words = [f for f in result.for_bus("u", Level.WORD) if f.kind == "byte"]
    values = [f.data["value"] for f in words]
    assert values[-(len(PAYLOAD) - 1):] == PAYLOAD[1:]
    assert len(values) <= len(PAYLOAD)
    if len(values) == len(PAYLOAD):
        # A leftover partial character that happens to frame cleanly is flagged.
        assert words[0].data.get("uncertain") is True
    assert not any(f.data.get("uncertain") for f in words[1:])


def test_capture_starts_low_mid_byte() -> None:
    t, v = uart_waveform([0x00, 0x42], BAUD, gap_bits=12, idle_bits=0.0)
    cut = int(2.5 * 16)  # inside the zero data bits
    assert _bytes(_decode((t[cut:], v[cut:]))) == [0x42]


def test_capture_ends_mid_byte() -> None:
    t, v = uart_waveform(PAYLOAD, BAUD, gap_bits=2, idle_bits=5)
    bits_to_last = 5 + (len(PAYLOAD) - 1) * 12
    cut = int((bits_to_last + 5.2) * 16)
    result = _decode((t[:cut], v[:cut]))
    words = result.for_bus("u", Level.WORD)
    assert _bytes(result) == PAYLOAD[:-1]
    assert words[-1].kind == "truncated"
    assert words[-1].error


def test_packet_grouping() -> None:
    wave = uart_waveform([0x41, 0x42, 0x43], BAUD, gap_bits=0.5)
    t2, v2 = uart_waveform([0x44, 0x45], BAUD, gap_bits=0.5, seed=1)
    t = np.concatenate([wave[0], t2 + wave[0][-1] + (wave[0][1] - wave[0][0])])
    v = np.concatenate([wave[1], v2])
    result = _decode((t, v), packet_gap=3.0, display="ascii")
    packets = result.for_bus("u", Level.PACKET)
    assert [p.data["values"] for p in packets] == [[0x41, 0x42, 0x43], [0x44, 0x45]]
    assert packets[0].text[1] == "ABC"


def test_display_variants() -> None:
    result = _decode(uart_waveform([0x41, 0x07], BAUD, gap_bits=1), display="ascii")
    words = result.for_bus("u", Level.WORD)
    assert words[0].text[1:] == ("'A'", "A")
    assert words[1].text[1:] == ("0x07", "07")


def test_tx_and_rx_decoded_independently() -> None:
    rx = uart_waveform([0x11, 0x22], BAUD, gap_bits=1)
    tx = uart_waveform([0x33], BAUD, idle_bits=9, seed=2)
    bus = BusConfig("u", "UART", "uart", role_map={"rx": "CH1", "tx": "CH2"}, options={"baud": BAUD})
    result = run([bus], {"CH1": rx, "CH2": tx})
    by_role: dict[str, list[int]] = {"rx": [], "tx": []}
    for f in result.for_bus("u", Level.WORD):
        by_role[f.data["role"]].append(f.data["value"])
    assert by_role == {"rx": [0x11, 0x22], "tx": [0x33]}


def test_low_oversampling_warns() -> None:
    wave = uart_waveform(PAYLOAD, BAUD, samples_per_bit=3, noise=0.0, edge_fraction=0.0)
    result = _decode(wave)
    assert any("samples per bit" in w for w in result.warnings)


def test_no_roles_mapped_is_a_warning_not_a_crash() -> None:
    bus = BusConfig("u", "UART", "uart", options={"baud": BAUD})
    result = run([bus], {})
    assert result.frames == {}
    assert any("at least one" in w for w in result.warnings)


@pytest.mark.parametrize("baud", [9600, 115200, 921600])
def test_estimate_baud(baud: int) -> None:
    t, v = uart_waveform(PAYLOAD, baud, gap_bits=1)
    assert estimate_baud(digitize(t, v)) == baud
