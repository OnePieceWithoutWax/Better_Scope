"""Register-access mapper tests on synthetic SMBus/PMBus/SPI waveforms."""

from collections.abc import Sequence

import pytest

from better_scope.decode import BusConfig, DecodeResult, Level, run
from better_scope.decode.decoders.smbus import crc8
from better_scope.decode.regmap import Device, DeviceBinding, Field, Register, SpiLayout, annotate, map_frames
from tests.signals import I2cSegment, i2c_waveform, spi_waveform

S = I2cSegment
ADDR = 0x40

REGISTERS = (
    Register("VOUT_CFG", 0x1C, 8, fields=(
        Field("EN", 7, 1),
        Field("MODE", 5, 2, enum={0: "Off", 1: "PWM", 2: "Auto"}),
        Field("VSEL", 0, 5),
    )),
    Register("VOUT", 0x21, 16, fields=(Field("RANGE", 14, 2, enum={2: "2-5 V"}), Field("CODE", 0, 12))),
)


def _device(bus: str = "smbus", address: int | None = ADDR) -> Device:
    return Device("PMIC", bus=bus, address=address, registers={r.address: r for r in REGISTERS})


def _smbus(transactions: Sequence[Sequence[I2cSegment]], decoder: str = "smbus", **options: object) -> DecodeResult:
    scl, sda = i2c_waveform(transactions, 100e3, samples_per_bit=20)
    options.setdefault("pec", "off")
    bus = BusConfig("b", "SMBus", decoder, role_map={"scl": "CH1", "sda": "CH2"}, options=options)
    return run([bus], {"CH1": scl, "CH2": sda})


def _spi(transactions: Sequence[Sequence[tuple[int, int]]]) -> DecodeResult:
    w = spi_waveform(transactions, 1e6)
    bus = BusConfig("s", "SPI", "spi", role_map={"sclk": "CH1", "mosi": "CH2", "miso": "CH3", "cs": "CH4"})
    return run([bus], {"CH1": w["sclk"], "CH2": w["mosi"], "CH3": w["miso"], "CH4": w["cs"]})


# -- SMBus -------------------------------------------------------------------


def test_smbus_write_byte_named_fields() -> None:
    result = _smbus([[S(ADDR, data=[0x1C, 0xCC])]])
    (access,) = annotate(result, [DeviceBinding("b", _device())])
    assert (access.direction, access.register_name, access.value, access.device_address) == ("write", "VOUT_CFG", 0xCC, ADDR)
    assert access.labels[0] == "WR VOUT_CFG = 0xCC : EN=1, MODE=Auto, VSEL=0x0C"
    assert access.errors == ()
    (frame,) = result.for_bus("b", Level.SEMANTIC)
    assert frame.decoder_id == "regmap" and frame.long_text == access.labels[0]
    assert frame.data["fields"][1] == {"name": "MODE", "msb": 6, "lsb": 5, "value": 2, "label": "Auto"}


def test_smbus_read_word_little_endian() -> None:
    result = _smbus([[S(ADDR, data=[0x21]), S(ADDR, read=True, data=[0x34, 0x92])]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.direction == "read" and access.value == 0x9234
    assert access.labels[0] == "RD VOUT = 0x9234 : RANGE=2-5 V, RESERVED[13:12]=0x1, CODE=0x234"


def test_smbus_unknown_register_shows_raw() -> None:
    result = _smbus([[S(ADDR, data=[0x3F, 0x12])]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.register is None and access.labels[0] == "WR 0x3F = 0x12"
    assert access.errors == ("unknown register 0x3F",)


def test_smbus_size_mismatch_flagged() -> None:
    result = _smbus([[S(ADDR, data=[0x1C, 0x01, 0x02])]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.value == 0x0201 and any("size mismatch" in e for e in access.errors)


def test_smbus_other_address_not_mapped() -> None:
    result = _smbus([[S(0x22, data=[0x1C, 0xCC])], [S(ADDR, data=[0x1C, 0x00])]])
    accesses = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert [a.device_address for a in accesses] == [ADDR]


def test_smbus_binding_address_overrides_device() -> None:
    result = _smbus([[S(0x22, data=[0x1C, 0xCC])]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device(), address=0x22)])
    assert access.register_name == "VOUT_CFG"


def test_smbus_address_nack() -> None:
    result = _smbus([[S(ADDR, address_ack=False)]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.value is None and any("NACK" in e for e in access.errors)


def test_smbus_pec_failure_carried() -> None:
    message = [ADDR << 1, 0x1C, 0xCC]
    result = _smbus([[S(ADDR, data=[0x1C, 0xCC, crc8(message) ^ 1])]], pec="on")
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.value == 0xCC and any("PEC mismatch" in e for e in access.errors)


# -- PMBus -------------------------------------------------------------------


def test_pmbus_fallback_names_without_a_map() -> None:
    result = _smbus([[S(ADDR, data=[0x21, 0x00, 0x03])], [S(ADDR, data=[0x03])]], decoder="pmbus")
    accesses = annotate(result, [])
    assert [a.labels[0] for a in accesses] == ["WR VOUT_COMMAND = 0x0300", "WR CLEAR_FAULTS"]
    semantic = result.for_bus("b", Level.SEMANTIC)
    assert [f.decoder_id for f in semantic] == ["regmap", "regmap"]  # PMBus frames replaced


def test_pmbus_fallback_disabled() -> None:
    result = _smbus([[S(ADDR, data=[0x21, 0x00, 0x03])]], decoder="pmbus")
    assert map_frames(result.iter_frames(), [], auto_pmbus=False) == []


def test_pmbus_device_map_overrides_and_falls_back() -> None:
    mfr = Register("MFR_MODE", 0xD0, 8, fields=(Field("FAST", 0, 1),))
    device = Device("PM", bus="pmbus", address=ADDR, registers={0xD0: mfr})
    result = _smbus([[S(ADDR, data=[0xD0, 0x01])], [S(ADDR, data=[0x01, 0x80])]], decoder="pmbus")
    accesses = map_frames(result.iter_frames(), [DeviceBinding("b", device)])
    assert [a.labels[0] for a in accesses] == ["WR MFR_MODE = 0x01 : FAST=1", "WR OPERATION = 0x80"]


def test_smbus_device_has_no_pmbus_fallback() -> None:
    result = _smbus([[S(ADDR, data=[0x01, 0x80])]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("b", _device())])
    assert access.register is None


# -- SPI ---------------------------------------------------------------------


def test_spi_default_layout() -> None:
    # [R/W:1][ADDR:7][DATA:8], R=1: write VOUT_CFG=0xCC, then read it back on MISO.
    result = _spi([[(0x1C, 0x00), (0xCC, 0x00)], [(0x9C, 0x00), (0x00, 0xA0)]])
    accesses = annotate(result, [DeviceBinding("s", _device("spi", None))])
    assert [a.labels[0] for a in accesses] == [
        "WR VOUT_CFG = 0xCC : EN=1, MODE=Auto, VSEL=0x0C",
        "RD VOUT_CFG = 0xA0 : EN=1, MODE=PWM, VSEL=0x00",
    ]
    assert [a.device_address for a in accesses] == [None, None]


def test_spi_two_frames_in_one_transaction() -> None:
    result = _spi([[(0x1C, 0), (0xCC, 0), (0x1C, 0), (0x00, 0)]])
    accesses = map_frames(result.iter_frames(), [DeviceBinding("s", _device("spi", None))])
    assert [a.value for a in accesses] == [0xCC, 0x00]
    assert accesses[0].end <= accesses[1].start


def test_spi_custom_layout() -> None:
    # 24-bit frame: [ADDR:7 at 17][W/R at 16, 0 = read][DATA:16].
    layout = SpiLayout(frame_bits=24, rw_bit=16, read_value=0, addr_lsb=17, addr_bits=7, data_lsb=0, data_bits=16)
    write = (0x21 << 17) | (1 << 16) | 0x9234
    read = 0x21 << 17
    words = [[((f >> s) & 0xFF, (m >> s) & 0xFF) for s in (16, 8, 0)] for f, m in ((write, 0), (read, 0x8001))]
    result = _spi(words)
    accesses = map_frames(result.iter_frames(), [DeviceBinding("s", _device("spi", None), spi_layout=layout)])
    assert [(a.direction, a.register_name, a.value) for a in accesses] == [
        ("write", "VOUT", 0x9234), ("read", "VOUT", 0x8001),
    ]
    assert accesses[1].labels[0] == "RD VOUT = 0x8001 : RANGE=2-5 V, CODE=0x001"


def test_spi_frame_size_mismatch() -> None:
    result = _spi([[(0x1C, 0), (0xCC, 0), (0x00, 0)]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("s", _device("spi", None))])
    assert access.value is None and "layout expects multiples of 16" in access.errors[0]


def test_spi_unknown_register() -> None:
    result = _spi([[(0x05, 0), (0x12, 0)]])
    (access,) = map_frames(result.iter_frames(), [DeviceBinding("s", _device("spi", None))])
    assert access.labels[0] == "WR 0x05 = 0x12" and access.errors == ("unknown register 0x05",)


def test_spi_layout_validation() -> None:
    with pytest.raises(ValueError, match="do not fit"):
        SpiLayout(frame_bits=8)
