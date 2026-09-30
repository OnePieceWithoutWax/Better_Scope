"""I2C decoder -- the base layer that SMBus and PMBus stack on.

START is SDA falling while SCL is high, STOP is SDA rising while SCL is high.
Every other SCL high pulse clocks one bit, sampled at the SCL rising edge.
Nine bits make a byte, MSB first, plus its acknowledge (SDA low = ACK, high =
NACK). The first byte after a START or repeated START is the 7-bit address
followed by the R/W bit (1 = read).

The decoder walks SCL edges, so clock stretching (a target holding SCL low)
needs no special handling. A first byte of ``11110xx`` is a 10-bit address:
it is flagged as unsupported and the rest of that segment is skipped rather
than mis-decoded.

Source: NXP UM10204, I2C-bus specification and user manual, rev. 7.0 (2021).
"""

__lazy_modules__ = ["numpy"]

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from better_scope.decode.api import Decoder, Option, Role
from better_scope.decode.model import Frame, Level, LogicSignal

# Upper five bits of the first byte of a 10-bit address.
TEN_BIT_PREFIX = 0b11110


def format_address(address: int, read: bool, address_format: str) -> str:
    """Address label, 7-bit (``0x40``) or 8-bit with the R/W bit (``0x81``).

    Args:
        address: 7-bit address.
        read: The R/W bit (only shown in 8-bit form).
        address_format: ``"7-bit"`` or ``"8-bit"``.

    Returns:
        The label.
    """
    if address_format == "8-bit":
        return f"0x{(address << 1) | int(read):02X}"
    return f"0x{address:02X}"


@dataclass
class _Segment:
    """One addressed part of a transaction: START/Sr up to the next Sr/STOP."""

    address: int | None = None
    read: bool = False
    address_ack: bool = False
    address_span: tuple[float, float] | None = None
    data: list[int] = field(default_factory=list)
    acks: list[bool] = field(default_factory=list)
    spans: list[tuple[float, float]] = field(default_factory=list)
    ten_bit: bool = False

    def to_data(self) -> dict[str, Any]:
        """JSON-friendly form stored in the transaction frame."""
        return {
            "address": self.address,
            "read": self.read,
            "address_ack": self.address_ack,
            "address_span": list(self.address_span) if self.address_span else None,
            "data": list(self.data),
            "acks": list(self.acks),
            "spans": [list(s) for s in self.spans],
            "ten_bit": self.ten_bit,
        }


@dataclass
class _Transaction:
    """State of the transaction being decoded."""

    start: float
    segments: list[_Segment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    max_scl_low: float = 0.0


class I2cDecoder(Decoder):
    """I2C with 7-bit addressing, repeated START and clock stretching."""

    id = "i2c"
    name = "I2C"
    version = "1.0"
    description = "Inter-IC bus: START/STOP, 7-bit address + R/W, data bytes with ACK/NACK."
    roles = (
        Role("scl", "SCL", help="Clock line"),
        Role("sda", "SDA", help="Data line"),
    )
    options = (
        Option("address_format", "Address format", str, "7-bit", choices=("7-bit", "8-bit"),
               help="8-bit shows the address shifted left with the R/W bit"),
    )

    def decode(self, signals: dict[str, LogicSignal], opts: dict[str, Any]) -> Iterator[Frame]:
        """Decode START/STOP conditions, bytes and transactions."""
        scl, sda = signals["scl"], signals["sda"]
        t_end = min(scl.t_end, sda.t_end)

        # Conditions: SDA edges while SCL is high. Rising = STOP, falling = START.
        sda_t = sda.edge_times
        sda_rising = (np.arange(sda_t.size) % 2 == 0) != sda.initial
        on_high = scl.levels_at(sda_t) & (sda_t <= t_end)
        cond_t = sda_t[on_high]
        cond_stop = sda_rising[on_high]

        # Bit pulses: SCL rising edge to the next falling edge (or end of capture).
        scl_t = scl.edge_times
        scl_rising = (np.arange(scl_t.size) % 2 == 0) != scl.initial
        rises = scl_t[scl_rising & (scl_t <= t_end)]
        falls = scl_t[~scl_rising]
        next_fall = np.searchsorted(falls, rises, side="right")
        pulse_end = np.where(next_fall < falls.size, falls[np.minimum(next_fall, falls.size - 1)], t_end)
        prev_fall_idx = next_fall - 1
        low_start = np.where(prev_fall_idx >= 0, falls[np.maximum(prev_fall_idx, 0)], rises)
        # A pulse holding a START/STOP is that condition's clock, not a data bit.
        lo = np.searchsorted(cond_t, rises, side="right")
        hi = np.searchsorted(cond_t, pulse_end, side="left")
        keep = hi == lo
        rises, pulse_end, low_start = rises[keep], pulse_end[keep], low_start[keep]
        bit_values = sda.levels_at(rises)

        times = np.concatenate([cond_t, rises])
        order = np.argsort(times, kind="stable")
        n_cond = cond_t.size

        txn: _Transaction | None = None
        seg: _Segment | None = None
        bits: list[tuple[int, float, float]] = []  # (value, start, end)
        skipped_bits = 0

        for i in order:
            if i < n_cond:
                t = float(cond_t[i])
                if cond_stop[i]:
                    if txn is None:
                        yield self.frame(t, t, "stop", Level.BIT, text=("Stop", "P"))
                        continue
                    yield from self._incomplete_byte(bits, txn)
                    bits = []
                    yield self.frame(t, t, "stop", Level.BIT, text=("Stop", "P"))
                    yield self._transaction_frame(txn, t, complete=True, opts=opts)
                    txn, seg = None, None
                else:
                    if txn is None:
                        txn = _Transaction(start=t)
                        yield self.frame(t, t, "start", Level.BIT, text=("Start", "S"))
                    else:
                        yield from self._incomplete_byte(bits, txn)
                        yield self.frame(t, t, "repeated_start", Level.BIT, text=("Repeated start", "Sr"))
                    bits = []
                    seg = _Segment()
                    txn.segments.append(seg)
                continue

            j = i - n_cond
            if txn is None or seg is None:
                skipped_bits += 1
                continue
            if seg.ten_bit:
                continue
            start, end = float(low_start[j]), float(pulse_end[j])
            txn.max_scl_low = max(txn.max_scl_low, float(rises[j]) - start)
            bits.append((int(bit_values[j]), start, end))
            if len(bits) == 9:
                yield from self._byte(bits, seg, txn, opts)
                bits = []

        if txn is not None:
            yield from self._incomplete_byte(bits, txn)
            txn.errors.append("capture ended mid-transaction")
            yield self._transaction_frame(txn, t_end, complete=False, opts=opts)
        if skipped_bits:
            self.warn(f"{skipped_bits} bit(s) before the first START ignored (capture began mid-transaction)")

    def _byte(
        self, bits: list[tuple[int, float, float]], seg: _Segment, txn: _Transaction, opts: dict[str, Any]
    ) -> Iterator[Frame]:
        """Frames for eight data bits plus the acknowledge bit."""
        value = 0
        for index, (b, start, end) in enumerate(bits[:8]):
            value = (value << 1) | b
            yield self.frame(start, end, "bit", Level.BIT, data={"index": 7 - index, "value": b}, text=(str(b),))
        ack_bit, ack_start, ack_end = bits[8]
        ack = ack_bit == 0
        span = (bits[0][1], bits[7][2])

        if seg.address is None:
            if value >> 3 == TEN_BIT_PREFIX:
                seg.ten_bit = True
                msg = "10-bit addressing is not supported"
                txn.errors.append(msg)
                yield self.frame(span[0], ack_end, "ten_bit_address", Level.WORD, data={"value": value},
                                 text=(f"10-bit address (0x{value:02X}...) not supported", "10-bit", "!"),
                                 error=msg)
                return
            seg.address, seg.read, seg.address_ack, seg.address_span = value >> 1, bool(value & 1), ack, span
            addr = format_address(seg.address, seg.read, opts["address_format"])
            rw = "R" if seg.read else "W"
            error = None if ack else "address NACK"
            if error:
                txn.errors.append(f"address {addr} NACK")
            yield self.frame(span[0], span[1], "address", Level.WORD,
                             data={"address": seg.address, "read": seg.read, "ack": ack},
                             text=(f"Address {addr} {'read' if seg.read else 'write'}", f"{addr} {rw}", rw),
                             error=error)
        else:
            seg.data.append(value)
            seg.acks.append(ack)
            seg.spans.append(span)
            # A controller NACKs the last byte of a read to end it; a NACK on a
            # written byte means the target refused it.
            error = None if ack or seg.read else "data NACK"
            if error:
                txn.errors.append(f"NACK on written byte {len(seg.data)}")
            yield self.frame(span[0], span[1], "data", Level.WORD,
                             data={"value": value, "read": seg.read, "ack": ack},
                             text=(f"{'Read' if seg.read else 'Write'} 0x{value:02X}", f"0x{value:02X}", f"{value:02X}"),
                             error=error)

        if ack:
            yield self.frame(ack_start, ack_end, "ack", Level.BIT, text=("ACK", "A"))
        else:
            error = None if seg.read and seg.data else "NACK"
            yield self.frame(ack_start, ack_end, "nack", Level.BIT, text=("NACK", "N"), error=error)

    def _incomplete_byte(self, bits: list[tuple[int, float, float]], txn: _Transaction) -> Iterator[Frame]:
        """Error frame for bits cut short by a START/STOP or the end of capture."""
        if not bits:
            return
        msg = f"incomplete byte ({len(bits)} of 9 bits)"
        txn.errors.append(msg)
        yield self.frame(bits[0][1], bits[-1][2], "incomplete_byte", Level.WORD,
                         data={"bits": [b for b, _, _ in bits]},
                         text=(f"Incomplete byte ({len(bits)} bits)", "Incomplete", "!"), error=msg)

    def _transaction_frame(self, txn: _Transaction, end: float, *, complete: bool, opts: dict[str, Any]) -> Frame:
        """Level-2 frame spanning START to STOP."""
        parts = []
        for seg in txn.segments:
            if seg.ten_bit:
                parts.append("10-bit address")
                continue
            if seg.address is None:
                continue
            head = f"{'R' if seg.read else 'W'} {format_address(seg.address, seg.read, opts['address_format'])}"
            if not seg.address_ack:
                head += " NACK"
            body = " ".join(f"{b:02X}" for b in seg.data)
            parts.append(f"{head}: {body}" if body else head)
        label = " | ".join(parts) or "empty"
        error = "; ".join(txn.errors) or None
        return self.frame(
            txn.start, end, "transaction", Level.PACKET,
            data={
                "segments": [seg.to_data() for seg in txn.segments],
                "complete": complete,
                "max_scl_low": txn.max_scl_low,
            },
            text=(label, parts[0] if parts else label, f"[{len(txn.segments)}]"),
            error=error,
        )
