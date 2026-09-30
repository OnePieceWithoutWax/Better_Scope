"""PMBus standard command set: code -> name, SMBus protocols, data size.

Transcribed from PMBus Power System Management Protocol Specification,
Part II -- Command Language, Revision 1.3.1 (13 March 2015), Appendix I,
Table 31 "Command Summary" (free download from pmbus.org). Codes the table
marks reserved are omitted. C4h-FDh are manufacturer specific
(``MFR_SPECIFIC_xx``); FEh and FFh are the extended-command prefixes.

``data_bytes`` excludes the PEC and a block transfer's byte count, as in the
spec; ``None`` means the spec says "Variable".

This table doubles as a PMBus device's default register map: the register
mapper (prompt 03) labels PMBus traffic from it when no user map is loaded,
and a user map overrides it by command code.
"""

from dataclasses import dataclass

from better_scope.decode.decoders.smbus import BLOCK, CommandSize

# Manufacturer-specific command codes (PMBus 1.3.1 Table 31).
MFR_SPECIFIC_CODES = range(0xC4, 0xFE)
# Extended-command prefixes: a second command byte follows.
EXTENDED_COMMANDS: dict[int, str] = {0xFE: "MFR_SPECIFIC_COMMAND_EXT", 0xFF: "PMBUS_COMMAND_EXT"}


@dataclass(frozen=True)
class PmbusCommand:
    """One standard PMBus command.

    Attributes:
        code: Command code.
        name: Spec name, e.g. ``VOUT_COMMAND``.
        write: SMBus protocol used to write it (an ``smbus.TYPE_NAMES`` key),
            or ``None`` if it cannot be written.
        read: SMBus protocol used to read it, or ``None``.
        data_bytes: Data bytes, or ``None`` if variable.
    """

    code: int
    name: str
    write: str | None
    read: str | None
    data_bytes: int | None


_TABLE: tuple[PmbusCommand, ...] = (
    PmbusCommand(0x00, "PAGE", "write_byte", "read_byte", 1),
    PmbusCommand(0x01, "OPERATION", "write_byte", "read_byte", 1),
    PmbusCommand(0x02, "ON_OFF_CONFIG", "write_byte", "read_byte", 1),
    PmbusCommand(0x03, "CLEAR_FAULTS", "send_byte", None, 0),
    PmbusCommand(0x04, "PHASE", "write_byte", "read_byte", 1),
    PmbusCommand(0x05, "PAGE_PLUS_WRITE", "block_write", None, None),
    PmbusCommand(0x06, "PAGE_PLUS_READ", None, "block_process_call", None),
    PmbusCommand(0x07, "ZONE_CONFIG", "write_word", "read_word", 2),
    PmbusCommand(0x08, "ZONE_ACTIVE", "write_word", "read_word", 2),
    PmbusCommand(0x10, "WRITE_PROTECT", "write_byte", "read_byte", 1),
    PmbusCommand(0x11, "STORE_DEFAULT_ALL", "send_byte", None, 0),
    PmbusCommand(0x12, "RESTORE_DEFAULT_ALL", "send_byte", None, 0),
    PmbusCommand(0x13, "STORE_DEFAULT_CODE", "write_byte", None, 1),
    PmbusCommand(0x14, "RESTORE_DEFAULT_CODE", "write_byte", None, 1),
    PmbusCommand(0x15, "STORE_USER_ALL", "send_byte", None, 0),
    PmbusCommand(0x16, "RESTORE_USER_ALL", "send_byte", None, 0),
    PmbusCommand(0x17, "STORE_USER_CODE", "write_byte", None, 1),
    PmbusCommand(0x18, "RESTORE_USER_CODE", "write_byte", None, 1),
    PmbusCommand(0x19, "CAPABILITY", None, "read_byte", 1),
    PmbusCommand(0x1A, "QUERY", None, "block_process_call", 1),
    PmbusCommand(0x1B, "SMBALERT_MASK", "write_word", "block_process_call", 2),
    PmbusCommand(0x20, "VOUT_MODE", "write_byte", "read_byte", 1),
    PmbusCommand(0x21, "VOUT_COMMAND", "write_word", "read_word", 2),
    PmbusCommand(0x22, "VOUT_TRIM", "write_word", "read_word", 2),
    PmbusCommand(0x23, "VOUT_CAL_OFFSET", "write_word", "read_word", 2),
    PmbusCommand(0x24, "VOUT_MAX", "write_word", "read_word", 2),
    PmbusCommand(0x25, "VOUT_MARGIN_HIGH", "write_word", "read_word", 2),
    PmbusCommand(0x26, "VOUT_MARGIN_LOW", "write_word", "read_word", 2),
    PmbusCommand(0x27, "VOUT_TRANSITION_RATE", "write_word", "read_word", 2),
    PmbusCommand(0x28, "VOUT_DROOP", "write_word", "read_word", 2),
    PmbusCommand(0x29, "VOUT_SCALE_LOOP", "write_word", "read_word", 2),
    PmbusCommand(0x2A, "VOUT_SCALE_MONITOR", "write_word", "read_word", 2),
    PmbusCommand(0x2B, "VOUT_MIN", "write_word", "read_word", 2),
    PmbusCommand(0x30, "COEFFICIENTS", None, "block_process_call", 5),
    PmbusCommand(0x31, "POUT_MAX", "write_word", "read_word", 2),
    PmbusCommand(0x32, "MAX_DUTY", "write_word", "read_word", 2),
    PmbusCommand(0x33, "FREQUENCY_SWITCH", "write_word", "read_word", 2),
    PmbusCommand(0x34, "POWER_MODE", "write_byte", "read_byte", 1),
    PmbusCommand(0x35, "VIN_ON", "write_word", "read_word", 2),
    PmbusCommand(0x36, "VIN_OFF", "write_word", "read_word", 2),
    PmbusCommand(0x37, "INTERLEAVE", "write_word", "read_word", 2),
    PmbusCommand(0x38, "IOUT_CAL_GAIN", "write_word", "read_word", 2),
    PmbusCommand(0x39, "IOUT_CAL_OFFSET", "write_word", "read_word", 2),
    PmbusCommand(0x3A, "FAN_CONFIG_1_2", "write_byte", "read_byte", 1),
    PmbusCommand(0x3B, "FAN_COMMAND_1", "write_word", "read_word", 2),
    PmbusCommand(0x3C, "FAN_COMMAND_2", "write_word", "read_word", 2),
    PmbusCommand(0x3D, "FAN_CONFIG_3_4", "write_byte", "read_byte", 1),
    PmbusCommand(0x3E, "FAN_COMMAND_3", "write_word", "read_word", 2),
    PmbusCommand(0x3F, "FAN_COMMAND_4", "write_word", "read_word", 2),
    PmbusCommand(0x40, "VOUT_OV_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x41, "VOUT_OV_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x42, "VOUT_OV_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x43, "VOUT_UV_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x44, "VOUT_UV_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x45, "VOUT_UV_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x46, "IOUT_OC_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x47, "IOUT_OC_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x48, "IOUT_OC_LV_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x49, "IOUT_OC_LV_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x4A, "IOUT_OC_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x4B, "IOUT_UC_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x4C, "IOUT_UC_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x4F, "OT_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x50, "OT_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x51, "OT_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x52, "UT_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x53, "UT_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x54, "UT_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x55, "VIN_OV_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x56, "VIN_OV_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x57, "VIN_OV_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x58, "VIN_UV_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x59, "VIN_UV_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x5A, "VIN_UV_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x5B, "IIN_OC_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x5C, "IIN_OC_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x5D, "IIN_OC_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x5E, "POWER_GOOD_ON", "write_word", "read_word", 2),
    PmbusCommand(0x5F, "POWER_GOOD_OFF", "write_word", "read_word", 2),
    PmbusCommand(0x60, "TON_DELAY", "write_word", "read_word", 2),
    PmbusCommand(0x61, "TON_RISE", "write_word", "read_word", 2),
    PmbusCommand(0x62, "TON_MAX_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x63, "TON_MAX_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x64, "TOFF_DELAY", "write_word", "read_word", 2),
    PmbusCommand(0x65, "TOFF_FALL", "write_word", "read_word", 2),
    PmbusCommand(0x66, "TOFF_MAX_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x68, "POUT_OP_FAULT_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x69, "POUT_OP_FAULT_RESPONSE", "write_byte", "read_byte", 1),
    PmbusCommand(0x6A, "POUT_OP_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x6B, "PIN_OP_WARN_LIMIT", "write_word", "read_word", 2),
    PmbusCommand(0x78, "STATUS_BYTE", "write_byte", "read_byte", 1),
    PmbusCommand(0x79, "STATUS_WORD", "write_word", "read_word", 2),
    PmbusCommand(0x7A, "STATUS_VOUT", "write_byte", "read_byte", 1),
    PmbusCommand(0x7B, "STATUS_IOUT", "write_byte", "read_byte", 1),
    PmbusCommand(0x7C, "STATUS_INPUT", "write_byte", "read_byte", 1),
    PmbusCommand(0x7D, "STATUS_TEMPERATURE", "write_byte", "read_byte", 1),
    PmbusCommand(0x7E, "STATUS_CML", "write_byte", "read_byte", 1),
    PmbusCommand(0x7F, "STATUS_OTHER", "write_byte", "read_byte", 1),
    PmbusCommand(0x80, "STATUS_MFR_SPECIFIC", "write_byte", "read_byte", 1),
    PmbusCommand(0x81, "STATUS_FANS_1_2", "write_byte", "read_byte", 1),
    PmbusCommand(0x82, "STATUS_FANS_3_4", "write_byte", "read_byte", 1),
    PmbusCommand(0x83, "READ_KWH_IN", None, "read_32", 4),
    PmbusCommand(0x84, "READ_KWH_OUT", None, "read_32", 4),
    PmbusCommand(0x85, "READ_KWH_CONFIG", "write_word", "read_word", 2),
    PmbusCommand(0x86, "READ_EIN", None, "block_read", 5),
    PmbusCommand(0x87, "READ_EOUT", None, "block_read", 5),
    PmbusCommand(0x88, "READ_VIN", None, "read_word", 2),
    PmbusCommand(0x89, "READ_IIN", None, "read_word", 2),
    PmbusCommand(0x8A, "READ_VCAP", None, "read_word", 2),
    PmbusCommand(0x8B, "READ_VOUT", None, "read_word", 2),
    PmbusCommand(0x8C, "READ_IOUT", None, "read_word", 2),
    PmbusCommand(0x8D, "READ_TEMPERATURE_1", None, "read_word", 2),
    PmbusCommand(0x8E, "READ_TEMPERATURE_2", None, "read_word", 2),
    PmbusCommand(0x8F, "READ_TEMPERATURE_3", None, "read_word", 2),
    PmbusCommand(0x90, "READ_FAN_SPEED_1", None, "read_word", 2),
    PmbusCommand(0x91, "READ_FAN_SPEED_2", None, "read_word", 2),
    PmbusCommand(0x92, "READ_FAN_SPEED_3", None, "read_word", 2),
    PmbusCommand(0x93, "READ_FAN_SPEED_4", None, "read_word", 2),
    PmbusCommand(0x94, "READ_DUTY_CYCLE", None, "read_word", 2),
    PmbusCommand(0x95, "READ_FREQUENCY", None, "read_word", 2),
    PmbusCommand(0x96, "READ_POUT", None, "read_word", 2),
    PmbusCommand(0x97, "READ_PIN", None, "read_word", 2),
    PmbusCommand(0x98, "PMBUS_REVISION", None, "read_byte", 1),
    PmbusCommand(0x99, "MFR_ID", "block_write", "block_read", None),
    PmbusCommand(0x9A, "MFR_MODEL", "block_write", "block_read", None),
    PmbusCommand(0x9B, "MFR_REVISION", "block_write", "block_read", None),
    PmbusCommand(0x9C, "MFR_LOCATION", "block_write", "block_read", None),
    PmbusCommand(0x9D, "MFR_DATE", "block_write", "block_read", None),
    PmbusCommand(0x9E, "MFR_SERIAL", "block_write", "block_read", None),
    PmbusCommand(0x9F, "APP_PROFILE_SUPPORT", None, "block_read", None),
    PmbusCommand(0xA0, "MFR_VIN_MIN", None, "read_word", 2),
    PmbusCommand(0xA1, "MFR_VIN_MAX", None, "read_word", 2),
    PmbusCommand(0xA2, "MFR_IIN_MAX", None, "read_word", 2),
    PmbusCommand(0xA3, "MFR_PIN_MAX", None, "read_word", 2),
    PmbusCommand(0xA4, "MFR_VOUT_MIN", None, "read_word", 2),
    PmbusCommand(0xA5, "MFR_VOUT_MAX", None, "read_word", 2),
    PmbusCommand(0xA6, "MFR_IOUT_MAX", None, "read_word", 2),
    PmbusCommand(0xA7, "MFR_POUT_MAX", None, "read_word", 2),
    PmbusCommand(0xA8, "MFR_TAMBIENT_MAX", None, "read_word", 2),
    PmbusCommand(0xA9, "MFR_TAMBIENT_MIN", None, "read_word", 2),
    PmbusCommand(0xAA, "MFR_EFFICIENCY_LL", None, "block_read", 14),
    PmbusCommand(0xAB, "MFR_EFFICIENCY_HL", None, "block_read", 14),
    PmbusCommand(0xAC, "MFR_PIN_ACCURACY", None, "read_byte", 1),
    PmbusCommand(0xAD, "IC_DEVICE_ID", None, "block_read", None),
    PmbusCommand(0xAE, "IC_DEVICE_REV", None, "block_read", None),
    PmbusCommand(0xB0, "USER_DATA_00", "block_write", "block_read", None),
    PmbusCommand(0xB1, "USER_DATA_01", "block_write", "block_read", None),
    PmbusCommand(0xB2, "USER_DATA_02", "block_write", "block_read", None),
    PmbusCommand(0xB3, "USER_DATA_03", "block_write", "block_read", None),
    PmbusCommand(0xB4, "USER_DATA_04", "block_write", "block_read", None),
    PmbusCommand(0xB5, "USER_DATA_05", "block_write", "block_read", None),
    PmbusCommand(0xB6, "USER_DATA_06", "block_write", "block_read", None),
    PmbusCommand(0xB7, "USER_DATA_07", "block_write", "block_read", None),
    PmbusCommand(0xB8, "USER_DATA_08", "block_write", "block_read", None),
    PmbusCommand(0xB9, "USER_DATA_09", "block_write", "block_read", None),
    PmbusCommand(0xBA, "USER_DATA_10", "block_write", "block_read", None),
    PmbusCommand(0xBB, "USER_DATA_11", "block_write", "block_read", None),
    PmbusCommand(0xBC, "USER_DATA_12", "block_write", "block_read", None),
    PmbusCommand(0xBD, "USER_DATA_13", "block_write", "block_read", None),
    PmbusCommand(0xBE, "USER_DATA_14", "block_write", "block_read", None),
    PmbusCommand(0xBF, "USER_DATA_15", "block_write", "block_read", None),
    PmbusCommand(0xC0, "MFR_MAX_TEMP_1", "write_word", "read_word", 2),
    PmbusCommand(0xC1, "MFR_MAX_TEMP_2", "write_word", "read_word", 2),
    PmbusCommand(0xC2, "MFR_MAX_TEMP_3", "write_word", "read_word", 2),
)

COMMANDS: dict[int, PmbusCommand] = {cmd.code: cmd for cmd in _TABLE}

_WRITE_SIZES: dict[str, int] = {"send_byte": 0, "write_byte": 1, "write_word": 2, "block_write": BLOCK}
_READ_SIZES: dict[str, int] = {
    "read_byte": 1, "read_word": 2, "read_32": 4, "block_read": BLOCK, "block_process_call": BLOCK,
}


def command_name(code: int) -> str:
    """Spec name for ``code``: standard, ``MFR_SPECIFIC_xx``, extended, or ``RESERVED_xx``."""
    if code in COMMANDS:
        return COMMANDS[code].name
    if code in MFR_SPECIFIC_CODES:
        return f"MFR_SPECIFIC_{code:02X}"
    if code in EXTENDED_COMMANDS:
        return EXTENDED_COMMANDS[code]
    return f"RESERVED_{code:02X}"


def command_sizes() -> dict[int, CommandSize]:
    """SMBus size hints for every standard command (see ``smbus.COMMAND_SIZES_HINT``)."""
    return {
        cmd.code: CommandSize(
            write=_WRITE_SIZES.get(cmd.write) if cmd.write else None,
            read=_READ_SIZES.get(cmd.read) if cmd.read else None,
        )
        for cmd in _TABLE
    }
