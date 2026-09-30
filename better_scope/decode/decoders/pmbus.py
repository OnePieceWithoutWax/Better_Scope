"""PMBus decoder, stacked on SMBus.

Names each SMBus transaction's command from the standard command table
(``pmbus_commands.py``, PMBus Part II rev 1.3.1) and passes each command's
data size down to the SMBus layer as a hint, so SMBus can tell a PEC byte
from a data byte in ``pec = "auto"`` mode.

v1 shows payloads as raw hex. Numeric formats (LINEAR11, LINEAR16 with
VOUT_MODE, VID) and PAGE tracking are deferred. Extended commands (FEh/FFh)
are named and flagged, not decoded further.
"""

from collections.abc import Iterator
from typing import Any

from better_scope.decode.api import Decoder
from better_scope.decode.decoders.i2c import format_address
from better_scope.decode.decoders.pmbus_commands import (
    COMMANDS,
    EXTENDED_COMMANDS,
    MFR_SPECIFIC_CODES,
    command_name,
    command_sizes,
)
from better_scope.decode.decoders.smbus import COMMAND_SIZES_HINT
from better_scope.decode.model import Frame, Level


class PmbusDecoder(Decoder):
    """PMBus command naming on top of SMBus."""

    id = "pmbus"
    name = "PMBus"
    version = "1.0"
    description = "Power Management Bus: standard command names (PMBus 1.3.1), raw payloads."
    stacks_on = "smbus"

    @classmethod
    def hints_for_lower(cls, opts: dict[str, Any]) -> dict[str, Any]:
        """Tell SMBus how many data bytes each standard command carries."""
        return {COMMAND_SIZES_HINT: command_sizes()}

    def decode_frames(self, frames: list[Frame], opts: dict[str, Any]) -> Iterator[Frame]:
        """One semantic frame per SMBus transaction."""
        for f in frames:
            if f.kind != "transaction":
                continue
            d = f.data
            code: int | None = d["command"]
            payload: list[int] = d["data"]
            if d["type"] == "send_byte" and d["write_data"]:
                # PMBus uses Send Byte to issue a data-less command.
                code, payload = d["write_data"][0], []
            if code is None:
                yield self.frame(f.start, f.end, "transaction", Level.SEMANTIC, data=dict(d),
                                 text=f.text, error=f.error)
                continue

            direction = "read" if d["read"] else "write"
            name = command_name(code)
            extended = code in EXTENDED_COMMANDS
            reserved = not (code in COMMANDS or code in MFR_SPECIFIC_CODES or extended)
            hex_payload = " ".join(f"{b:02X}" for b in payload)
            if reserved:
                head = f"Command 0x{code:02X} (reserved) {direction}"
            elif extended:
                head = f"{name} {direction} (extended, not decoded)"
            else:
                head = f"{name} {direction}"
            addr = format_address(d["address"], d["read"], opts["address_format"])
            long_label = f"{addr} {head}" + (f": {hex_payload}" if hex_payload else "")
            yield self.frame(
                f.start, f.end, "command", Level.SEMANTIC,
                data={
                    "address": d["address"],
                    "command": code,
                    "name": name,
                    "direction": direction,
                    "transaction": d["type"],
                    "data": payload,
                    "pec_ok": d["pec_ok"],
                    "mfr_specific": code in MFR_SPECIFIC_CODES,
                    "extended": extended,
                    "reserved": reserved,
                },
                text=(long_label, head, name if not reserved else f"0x{code:02X}"),
                error=f.error,
            )
