"""SMBus decoder, stacked on I2C.

Classifies each I2C transaction as one of the SMBus protocols from its byte
counts and repeated-START/direction pattern, and checks the optional Packet
Error Code.

PEC is a CRC-8 (polynomial x^8 + x^2 + x + 1, i.e. 0x07, initial value 0)
over every byte of the message, address bytes with their R/W bit included,
appended by the device that sent the last data byte. Without knowing how
many data bytes a command carries, a trailing PEC is indistinguishable from
one more data byte. With ``pec = "auto"`` the decoder therefore uses the
``smbus.command_sizes`` hint from a stacked layer (PMBus provides one) when
it has one; otherwise it guesses from whether the last byte matches the CRC
and marks the result ``PEC?``.

Source: System Management Bus (SMBus) Specification, version 3.3.1
(2024-10-20), section 6.4 (Packet Error Checking), section 6.5 (bus protocols)
and the tTIMEOUT,MIN limit of 25 ms.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from better_scope.decode.api import Decoder, Option
from better_scope.decode.decoders.i2c import format_address
from better_scope.decode.model import Frame, Level

# CommandSize value for a count-prefixed (block) transfer.
BLOCK = -1
# Hint key: dict[int, CommandSize], command code -> expected data sizes.
COMMAND_SIZES_HINT = "smbus.command_sizes"
# tTIMEOUT,MIN: a single clock-low interval longer than this is a timeout.
TIMEOUT_MIN = 25e-3

TYPE_NAMES: dict[str, str] = {
    "quick_command": "Quick Command",
    "send_byte": "Send Byte",
    "receive_byte": "Receive Byte",
    "write_byte": "Write Byte",
    "write_word": "Write Word",
    "write_32": "Write 32",
    "write_64": "Write 64",
    "read_byte": "Read Byte",
    "read_word": "Read Word",
    "read_32": "Read 32",
    "read_64": "Read 64",
    "block_write": "Block Write",
    "block_read": "Block Read",
    "process_call": "Process Call",
    "block_process_call": "Block Write-Block Read Process Call",
    "address_nack": "Address NACK",
    "incomplete": "Incomplete",
    "unknown": "Unrecognized",
}

# Data-byte count (after the command code) -> protocol.
_WRITE_TYPES: dict[int, str] = {
    0: "send_byte", 1: "write_byte", 2: "write_word", 4: "write_32", 8: "write_64", BLOCK: "block_write",
}
_READ_TYPES: dict[int, str] = {1: "read_byte", 2: "read_word", 4: "read_32", 8: "read_64", BLOCK: "block_read"}


@dataclass(frozen=True)
class CommandSize:
    """Data bytes a command carries, excluding command code, byte count and PEC.

    Attributes:
        write: Bytes after the command code when written (0 = Send Byte),
            :data:`BLOCK` for a block write, ``None`` if unknown/not writable.
        read: Bytes returned when read, :data:`BLOCK` for a block read or
            block process call, ``None`` if unknown/not readable.
    """

    write: int | None = None
    read: int | None = None


def crc8(data: Iterable[int], crc: int = 0) -> int:
    """SMBus PEC: CRC-8, polynomial 0x07, MSB first, no reflection.

    Args:
        data: Bytes in bus order.
        crc: Initial value (0 for SMBus).

    Returns:
        The CRC byte.
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


@dataclass
class _Parsed:
    """An I2C transaction interpreted as an SMBus protocol."""

    type: str
    read: bool = False
    command: int | None = None
    count: int | None = None
    write_data: list[int] = field(default_factory=list)
    read_data: list[int] = field(default_factory=list)
    pec: int | None = None
    pec_expected: int | None = None
    pec_guessed: bool = False
    pec_span: list[float] | None = None
    errors: list[str] = field(default_factory=list)


def _decide_pec(mode: str, part: list[int], prefix: list[int], expected: int | None) -> tuple[bool, bool, str | None]:
    """Whether the last byte of ``part`` is a PEC.

    Args:
        mode: ``"off"``, ``"on"`` or ``"auto"``.
        part: Bytes of the segment that may end in a PEC.
        prefix: Every message byte before ``part`` (addresses included).
        expected: Expected length of ``part`` without a PEC, if known.

    Returns:
        ``(has_pec, guessed, error)``.
    """
    n = len(part)
    if mode == "off":
        mismatch = expected is not None and n != expected
        return False, False, (f"expected {expected} bytes, got {n}" if mismatch else None)
    if mode == "on":
        if expected is not None:
            if n == expected + 1:
                return True, False, None
            return n >= 2, False, f"expected {expected} bytes + PEC, got {n}"
        return (True, False, None) if n >= 2 else (False, False, "PEC missing")
    error = None
    if expected is not None:
        if n == expected:
            return False, False, None
        if n == expected + 1:
            return True, False, None
        error = f"expected {expected} bytes (+ optional PEC), got {n}"
    matches = n >= 2 and crc8([*prefix, *part[:-1]]) == part[-1]
    return matches, True, error


def classify(segments: list[dict[str, Any]], pec_mode: str, sizes: dict[int, CommandSize]) -> _Parsed:
    """Interpret the segments of a complete, ACKed I2C transaction.

    Some protocols are byte-for-byte identical (a Block Write of one byte and
    a Write Word). A size hint for the command settles it; without one the
    fixed-size protocol wins.

    Args:
        segments: ``segments`` from an I2C transaction frame.
        pec_mode: ``"off"``, ``"on"`` or ``"auto"``.
        sizes: Command code -> expected sizes (may be empty).

    Returns:
        The parsed transaction.
    """
    first = segments[0]
    addr = first["address"]
    addr_w, addr_r = addr << 1, (addr << 1) | 1

    if len(segments) == 1 and not first["read"]:
        w = list(first["data"])
        if not w:
            return _Parsed("quick_command")
        size = sizes.get(w[0])
        expected: int | None = None
        if size is not None and size.write is not None:
            if size.write != BLOCK:
                expected = 1 + size.write
            elif len(w) >= 2:
                expected = 2 + w[1]
        has_pec, guessed, error = _decide_pec(pec_mode, w, [addr_w], expected)
        body = w[:-1] if has_pec else w
        p = _Parsed("unknown", pec_guessed=guessed and has_pec)
        if error:
            p.errors.append(error)
        if has_pec:
            p.pec, p.pec_expected = w[-1], crc8([addr_w, *body])
            p.pec_span = first["spans"][-1]
        m = len(body)
        if expected is not None and size is not None and m == expected and size.write in _WRITE_TYPES:
            p.type = _WRITE_TYPES[size.write]
        elif m - 1 in _WRITE_TYPES and m - 1 != BLOCK:
            p.type = _WRITE_TYPES[m - 1]
        elif m >= 2 and body[1] == m - 2:
            p.type = "block_write"
        if p.type == "send_byte":
            p.write_data = body
        elif p.type == "block_write":
            p.command, p.count, p.write_data = body[0], body[1], body[2:]
        elif body:
            p.command, p.write_data = body[0], body[1:]
        return p

    if len(segments) == 1:
        r = list(first["data"])
        if not r:
            return _Parsed("quick_command", read=True)
        has_pec, guessed, error = _decide_pec(pec_mode, r, [addr_r], None)
        body = r[:-1] if has_pec else r
        p = _Parsed("receive_byte" if len(body) == 1 else "unknown", read=True, read_data=body,
                    pec_guessed=guessed and has_pec)
        if error:
            p.errors.append(error)
        if has_pec:
            p.pec, p.pec_expected, p.pec_span = r[-1], crc8([addr_r, *body]), first["spans"][-1]
        return p

    second = segments[1]
    if len(segments) != 2 or first["read"] or not second["read"] or second["address"] != addr or not first["data"]:
        return _Parsed("unknown")

    w, r = list(first["data"]), list(second["data"])
    cmd, tail = w[0], w[1:]
    size = sizes.get(cmd)
    hint_read = size.read if size is not None else None
    if not tail:
        kind = "read"
    elif hint_read == BLOCK and tail[0] == len(tail) - 1:
        kind = "block_process_call"
    elif len(tail) == 2:
        kind = "process_call"
    elif tail[0] == len(tail) - 1:
        kind = "block_process_call"
    else:
        return _Parsed("unknown")

    expected = None
    if hint_read is not None and hint_read != BLOCK:
        expected = hint_read
    elif (hint_read == BLOCK or kind == "block_process_call") and r:
        expected = 1 + r[0]
    elif kind == "process_call":
        expected = 2
    has_pec, guessed, error = _decide_pec(pec_mode, r, [addr_w, *w, addr_r], expected)
    body = r[:-1] if has_pec else r
    p = _Parsed(kind, read=True, command=cmd, pec_guessed=guessed and has_pec)
    if error:
        p.errors.append(error)
    if has_pec:
        p.pec, p.pec_expected = r[-1], crc8([addr_w, *w, addr_r, *body])
        p.pec_span = second["spans"][-1]
    m = len(body)
    if kind == "read":
        if expected is not None and m == expected and hint_read in _READ_TYPES:
            p.type = _READ_TYPES[hint_read]
        elif m in _READ_TYPES:
            p.type = _READ_TYPES[m]
        elif m >= 1 and body[0] == m - 1:
            p.type = "block_read"
        else:
            p.type = "unknown"
    if p.type in ("block_read", "block_process_call") and body:
        p.count, p.read_data = body[0], body[1:]
    else:
        p.read_data = body
    if kind == "process_call":
        p.write_data = tail
    elif kind == "block_process_call":
        p.write_data = tail[1:]
    return p


class SmbusDecoder(Decoder):
    """SMBus protocol classification and PEC checking on top of I2C."""

    id = "smbus"
    name = "SMBus"
    version = "1.0"
    description = "System Management Bus: protocol type, command code, PEC check (SMBus 3.3.1)."
    stacks_on = "i2c"
    options = (
        Option("pec", "PEC", str, "auto", choices=("auto", "on", "off"),
               help="auto: use command sizes from a stacked layer (PMBus), else guess and mark 'PEC?'"),
        Option("smbus_timeout", "Check timeout", bool, False,
               help="Flag any clock-low interval longer than tTIMEOUT,MIN (25 ms)"),
    )

    def decode_frames(self, frames: list[Frame], opts: dict[str, Any]) -> Iterator[Frame]:
        """One level-2 frame per I2C transaction, plus PEC byte frames."""
        sizes: dict[int, CommandSize] = self.hints.get(COMMAND_SIZES_HINT, {})
        for f in frames:
            if f.kind != "transaction":
                continue
            segments = f.data["segments"]
            errors = [f.error] if f.error else []
            if not segments or any(s["ten_bit"] or s["address"] is None for s in segments):
                parsed = _Parsed("unknown")
            elif not f.data["complete"]:
                parsed = _Parsed("incomplete")
            elif not all(s["address_ack"] for s in segments):
                parsed = _Parsed("address_nack", read=segments[0]["read"])
            else:
                parsed = classify(segments, opts["pec"], sizes)
            errors += parsed.errors
            if opts["smbus_timeout"] and f.data["max_scl_low"] > TIMEOUT_MIN:
                errors.append(f"clock low {f.data['max_scl_low'] * 1e3:.1f} ms exceeds tTIMEOUT,MIN (25 ms)")

            pec_ok: bool | None = None
            if parsed.pec is not None:
                pec_ok = parsed.pec == parsed.pec_expected
                if not pec_ok:
                    errors.append(f"PEC mismatch (got 0x{parsed.pec:02X}, expected 0x{parsed.pec_expected:02X})")
                if parsed.pec_span is not None:
                    yield self._pec_frame(parsed, pec_ok)

            address = segments[0]["address"] if segments else None
            yield self.frame(
                f.start, f.end, "transaction", Level.PACKET,
                data={
                    "address": address,
                    "type": parsed.type,
                    "read": parsed.read,
                    "command": parsed.command,
                    "count": parsed.count,
                    "write_data": parsed.write_data,
                    "read_data": parsed.read_data,
                    "data": parsed.read_data if parsed.read else parsed.write_data,
                    "pec": parsed.pec,
                    "pec_ok": pec_ok,
                    "pec_guessed": parsed.pec_guessed,
                },
                text=self._labels(parsed, address, pec_ok, opts),
                error="; ".join(errors) or None,
            )

    def _pec_frame(self, parsed: _Parsed, pec_ok: bool) -> Frame:
        """Word-level frame on the PEC byte."""
        start, end = parsed.pec_span  # type: ignore[misc]
        suffix = "?" if parsed.pec_guessed else ""
        if pec_ok:
            return self.frame(start, end, "pec", Level.WORD, data={"pec": parsed.pec, "ok": True},
                              text=(f"PEC{suffix} 0x{parsed.pec:02X} ok", f"PEC{suffix}"))
        return self.frame(start, end, "pec_error", Level.WORD,
                          data={"pec": parsed.pec, "ok": False, "expected": parsed.pec_expected},
                          text=(f"PEC 0x{parsed.pec:02X} != 0x{parsed.pec_expected:02X}", "PEC!", "!"),
                          error="PEC mismatch")

    @staticmethod
    def _labels(parsed: _Parsed, address: int | None, pec_ok: bool | None, opts: dict[str, Any]) -> tuple[str, ...]:
        """Label variants for a transaction frame, longest first."""
        name = TYPE_NAMES[parsed.type]
        parts = [name]
        if address is not None:
            parts.append(format_address(address, parsed.read, opts["address_format"]))
        if parsed.command is not None:
            parts.append(f"cmd 0x{parsed.command:02X}")
        if parsed.type in ("process_call", "block_process_call"):
            parts.append(" ".join(f"{b:02X}" for b in parsed.write_data) + " ->")
            parts.append(" ".join(f"{b:02X}" for b in parsed.read_data))
        elif parsed.read_data or parsed.write_data:
            parts.append(" ".join(f"{b:02X}" for b in (parsed.read_data or parsed.write_data)))
        if pec_ok is not None:
            parts.append(("PEC? ok" if parsed.pec_guessed else "PEC ok") if pec_ok else "PEC error")
        short = name if parsed.command is None else f"{name} 0x{parsed.command:02X}"
        return (" ".join(parts), short, name)
