"""Digital (TLP058) sources: DALL payload splitting, transfer, decode, save/load."""

from pathlib import Path

import numpy as np
import pytest

from better_scope.core import BetterScope
from better_scope.decode import BusConfig, Level, run
from better_scope.digital import (
    curve_to_ints,
    digital_sources,
    is_digital_probe,
    parse_digital_source,
    split_bits,
)
from better_scope.waveform_io import load_waveforms, save_waveforms
from tests.signals import levels_waveform, spi_waveform, uart_levels

BAUD = 115200
SAMPLE_RATE = BAUD * 16


def _pack(bits: dict[int, np.ndarray], n: int) -> np.ndarray:
    """Build DALL sample integers from bit-number -> boolean array."""
    values = np.zeros(n, dtype=np.uint16)
    for bit, levels in bits.items():
        values |= levels.astype(np.uint16) << bit
    return values


class FakeConnection:
    """pyvisa resource: returns the queued ``CURVe?`` payload."""

    def __init__(self) -> None:
        self.payload = b""

    def read_binary_values(self, datatype: str, container: type) -> bytes:
        assert datatype == "s" and container is bytes
        return self.payload


class FakeAdapter:
    def __init__(self) -> None:
        self.connection = FakeConnection()


class FakeChannel:
    def __init__(self, probe: str) -> None:
        self.probe_type = probe
        self.enable = True


class FakeAnalogWaveforms:
    def get_multiple_waveforms(self, sources: list[str]) -> dict:
        t = np.arange(4) * 1e-6
        return {s: (t, np.ones(4)) for s in sources}


class FakeInstrument:
    """Scope with a logic probe on CH2; answers the DALL transfer queries."""

    def __init__(self, byte_width: int = 1, byte_order: str = "LSB") -> None:
        self.channels = (FakeChannel("ANALOG"), FakeChannel("DIGITAL"), FakeChannel('"ANALOG"'))
        self.analog_channels_count = len(self.channels)
        self.adapter = FakeAdapter()
        self.waveforms = FakeAnalogWaveforms()
        self.written: list[str] = []
        self.byte_width = byte_width
        self.byte_order = byte_order
        self.x_incr = 1.0 / SAMPLE_RATE
        self.x_zero = -1e-4

    def ask(self, command: str) -> str:
        return {
            "DATa:ENCdg?": "RIBINARY\n",
            "WFMOutpre:BYT_Nr?": f"{self.byte_width}\n",
            "WFMOutpre:BYT_Or?": f"{self.byte_order}\n",
            "WFMOutpre:XINcr?": f"{self.x_incr!r}\n",
            "WFMOutpre:XZEro?": f"{self.x_zero!r}\n",
        }[command]

    def write(self, command: str) -> None:
        self.written.append(command)

    def queue_curve(self, values: np.ndarray) -> None:
        endian = ">" if self.byte_order == "MSB" else "<"
        self.adapter.connection.payload = values.astype(f"{endian}u{self.byte_width}").tobytes()


def _scope(instrument: FakeInstrument) -> BetterScope:
    scope = BetterScope()
    scope._instrument = instrument
    scope.digital_channels = scope.detect_digital_channels()
    return scope


def test_source_names() -> None:
    assert digital_sources(3) == [f"CH3_D{i}" for i in range(8)]
    assert parse_digital_source("CH3_D7") == (3, 7)
    assert parse_digital_source("FILE:ch1_d0") == (1, 0)
    assert parse_digital_source("CH1") is None
    assert parse_digital_source("CH1_D8") is None
    assert is_digital_probe('"DIGITAL"') and not is_digital_probe("ANALOG") and not is_digital_probe(None)


def test_split_known_payload() -> None:
    payload = bytes([0b00000001, 0b10000010, 0b11111111, 0b00000000])
    bits = split_bits(curve_to_ints(payload, 1, "LSB"))
    assert len(bits) == 8
    assert bits[0].tolist() == [True, False, True, False]
    assert bits[1].tolist() == [False, True, True, False]
    assert bits[7].tolist() == [False, True, True, False]
    assert bits[3].tolist() == [False, False, True, False]
    assert all(b.dtype == bool for b in bits)


@pytest.mark.parametrize(("width", "order"), [(2, "LSB"), (2, "MSB"), (4, "LSB")])
def test_wider_samples_use_low_bits(width: int, order: str) -> None:
    values = np.array([0x0001, 0x0180, 0xFF00], dtype=np.uint32)
    endian = ">" if order == "MSB" else "<"
    payload = values.astype(f"{endian}u{width}").tobytes()
    bits = split_bits(curve_to_ints(payload, width, order))
    assert bits[0].tolist() == [True, False, False]
    assert bits[7].tolist() == [False, True, False]


def test_bad_payloads() -> None:
    with pytest.raises(ValueError, match="width"):
        curve_to_ints(b"\x00\x00\x00", 3, "LSB")
    with pytest.raises(ValueError, match="whole number"):
        curve_to_ints(b"\x00\x00\x00", 2, "LSB")


def test_detects_probe_and_lists_sources() -> None:
    scope = _scope(FakeInstrument())
    assert scope.digital_channels == [2]
    assert scope.sources() == ["CH1", *digital_sources(2), "CH3"]
    assert scope.enabled_sources() == ["CH1", *digital_sources(2), "CH3"]


def test_dall_transfer_splits_bits_and_restores_encoding() -> None:
    instr = FakeInstrument()
    scope = _scope(instr)
    rng = np.random.default_rng(1)
    truth = {bit: rng.random(1000) > 0.5 for bit in range(8)}
    instr.queue_curve(_pack(truth, 1000))

    waves = scope.acquire_waveforms(["CH2_D5", "CH1", "CH2_D0"])

    assert list(waves) == ["CH2_D5", "CH1", "CH2_D0"]
    assert instr.written == ["DATa:SOUrce CH2_DALL", "DATa:ENCdg SRPbinary", "CURVe?", "DATa:ENCdg RIBINARY"]
    t, d5 = waves["CH2_D5"]
    assert d5.dtype == bool and np.array_equal(d5, truth[5])
    assert np.array_equal(waves["CH2_D0"][1], truth[0])
    assert t[0] == pytest.approx(instr.x_zero) and t[1] - t[0] == pytest.approx(instr.x_incr)
    assert waves["CH1"][1].dtype != bool


def test_uart_and_spi_decode_from_digital_bits() -> None:
    instr = FakeInstrument(byte_width=2)
    scope = _scope(instr)
    # UART on D3.
    segs = [(lv, bits / BAUD) for lv, bits in uart_levels([0x48, 0x69, 0x00, 0xFF])]
    _, v = levels_waveform(segs, SAMPLE_RATE)
    uart = v > 1.65
    # SPI mode 0 on D0 (SCLK), D1 (MOSI), D2 (CS) at the same sample rate.
    spi = spi_waveform([[(0xA5, 0), (0x3C, 0)]], BAUD, noise=0.0, edge_fraction=0.0, samples_per_bit=16)
    n = max(uart.size, spi["sclk"][1].size)

    def pad(levels: np.ndarray, idle: bool) -> np.ndarray:
        return np.concatenate([levels, np.full(n - levels.size, idle)])

    bits = {
        3: pad(uart, True),
        0: pad(spi["sclk"][1] > 1.65, False),
        1: pad(spi["mosi"][1] > 1.65, False),
        2: pad(spi["cs"][1] > 1.65, True),
    }
    instr.queue_curve(_pack(bits, n))
    waves = scope.acquire_waveforms([*digital_sources(2)])

    uart_bus = BusConfig("u", "UART", "uart", role_map={"rx": "CH2_D3"}, options={"baud": BAUD})
    spi_bus = BusConfig("s", "SPI", "spi", role_map={"sclk": "CH2_D0", "mosi": "CH2_D1", "cs": "CH2_D2"})
    result = run([uart_bus, spi_bus], waves)

    assert [f.data["value"] for f in result.for_bus("u", Level.WORD)] == [0x48, 0x69, 0x00, 0xFF]
    assert [t.data["mosi"] for t in result.for_bus("s", Level.PACKET)] == [[0xA5, 0x3C]]
    assert result.errors == []
    assert not any("threshold" in w or "flat" in w for w in result.warnings)


def test_npz_packs_digital_sources(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    t = np.arange(100_001) * 1e-8
    bits = rng.random(t.size) > 0.5
    analog = np.sin(t * 1e6)
    path = save_waveforms(tmp_path / "mixed.npz", {"CH1": (t, analog), "CH2_D0": (t, bits)})

    loaded, meta = load_waveforms(path)
    assert loaded["CH2_D0"][1].dtype == bool
    assert np.array_equal(loaded["CH2_D0"][1], bits)
    assert np.allclose(loaded["CH2_D0"][0], t)
    assert meta["units"] == {"CH1": "V", "CH2_D0": "logic"}
    with np.load(path) as data:
        assert "s1_samples" not in data.files
        assert data["s1_packed"].nbytes == (t.size + 7) // 8


def test_csv_reads_digital_columns_as_bool(tmp_path: Path) -> None:
    t = np.arange(8) * 1e-6
    bits = np.array([0, 1, 1, 0, 1, 0, 0, 1], dtype=bool)
    path = save_waveforms(tmp_path / "mixed.csv", {"CH1": (t, np.ones(8) * 0.5), "CH2_D1": (t, bits)})
    loaded, _ = load_waveforms(path)
    assert loaded["CH2_D1"][1].dtype == bool and np.array_equal(loaded["CH2_D1"][1], bits)
    assert loaded["CH1"][1].dtype == float
