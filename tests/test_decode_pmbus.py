"""PMBus decoder and command-table tests."""

from collections.abc import Sequence

from better_scope.decode import BusConfig, DecodeResult, Frame, Level, run
from better_scope.decode.decoders.pmbus_commands import COMMANDS, command_name, command_sizes
from better_scope.decode.decoders.smbus import BLOCK, CommandSize
from tests.signals import I2cSegment, i2c_waveform

ADDR = 0x40
S = I2cSegment


def _commands(transactions: Sequence[Sequence[I2cSegment]], **options: object) -> list[Frame]:
    scl, sda = i2c_waveform(transactions, 400e3, samples_per_bit=20)
    bus = BusConfig("p", "PMBus", "pmbus", role_map={"scl": "CH1", "sda": "CH2"}, options=options)
    result: DecodeResult = run([bus], {"CH1": scl, "CH2": sda})
    return result.for_bus("p", Level.SEMANTIC)


def test_known_commands_named() -> None:
    frames = _commands([
        [S(ADDR, data=[0x21, 0x00, 0x1A])],
        [S(ADDR, data=[0x8B]), S(ADDR, read=True, data=[0x9A, 0x19])],
        [S(ADDR, data=[0x03])],
    ])
    assert [(f.data["name"], f.data["direction"], f.data["data"]) for f in frames] == [
        ("VOUT_COMMAND", "write", [0x00, 0x1A]),
        ("READ_VOUT", "read", [0x9A, 0x19]),
        ("CLEAR_FAULTS", "write", []),
    ]
    assert frames[0].long_text == "0x40 VOUT_COMMAND write: 00 1A"
    assert all(f.error is None for f in frames)


def test_mfr_specific_range_labeled() -> None:
    (frame,) = _commands([[S(ADDR, data=[0xD0, 0x01])]])
    assert frame.data["name"] == "MFR_SPECIFIC_D0"
    assert frame.data["mfr_specific"] is True and frame.data["reserved"] is False


def test_unknown_command_shown_raw() -> None:
    (frame,) = _commands([[S(ADDR, data=[0x09, 0x01])]])
    assert frame.data["reserved"] is True
    assert frame.short_text == "0x09"
    assert "reserved" in frame.long_text


def test_extended_command_flagged() -> None:
    (frame,) = _commands([[S(ADDR, data=[0xFF, 0x05, 0x01])]])
    assert frame.data["name"] == "PMBUS_COMMAND_EXT" and frame.data["extended"] is True
    assert "not decoded" in frame.long_text


def test_receive_byte_passes_through() -> None:
    (frame,) = _commands([[S(ADDR, read=True, data=[0x42])]], pec="off")
    assert frame.kind == "transaction" and frame.data["type"] == "receive_byte"


def test_command_table_matches_spec_samples() -> None:
    # Spot checks against PMBus 1.3.1 Part II Table 31.
    assert COMMANDS[0x00].name == "PAGE" and COMMANDS[0x00].write == "write_byte"
    assert COMMANDS[0x1B].write == "write_word" and COMMANDS[0x1B].read == "block_process_call"
    assert COMMANDS[0x79].name == "STATUS_WORD"
    assert COMMANDS[0x83].read == "read_32"
    assert COMMANDS[0xAA].data_bytes == 14
    assert command_name(0xC4) == "MFR_SPECIFIC_C4"
    assert command_name(0xFE) == "MFR_SPECIFIC_COMMAND_EXT"
    assert command_name(0x4D) == "RESERVED_4D"
    sizes = command_sizes()
    assert sizes[0x03] == CommandSize(write=0, read=None)
    assert sizes[0x99] == CommandSize(write=BLOCK, read=BLOCK)
    assert 0xD0 not in sizes
