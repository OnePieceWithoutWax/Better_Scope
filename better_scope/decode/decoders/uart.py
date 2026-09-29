"""UART / RS-232 decoder -- also the reference example for plugin authors.

Asynchronous serial: the line idles high (mark). Each character is a start bit
(low), 5-9 data bits, an optional parity bit, and 1, 1.5 or 2 stop bits
(high). RS-232 drivers invert this (mark is a negative voltage), which the
``invert`` option undoes.

The receiver works like a hardware UART: it waits for a falling edge, samples
every bit at its centre timed from that edge, then looks for the next falling
edge after the middle of the first stop bit, so it re-syncs on every start bit.
"""

__lazy_modules__ = ["numpy"]

from collections.abc import Iterator
from typing import Any

import numpy as np

from better_scope.decode.api import Decoder, DecodeError, Option, Role
from better_scope.decode.digitize import oversampling_warning
from better_scope.decode.model import Frame, Level, LogicSignal

# Rates estimate_baud() snaps to.
STANDARD_BAUD_RATES: tuple[int, ...] = (
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800,
    115200, 230400, 250000, 460800, 500000, 921600, 1000000, 1500000, 2000000,
    3000000, 4000000,
)


def estimate_baud(signal: LogicSignal, snap_tolerance: float = 0.05) -> float | None:
    """Estimate the baud rate from the shortest pulse on a UART line.

    Pulses shorter than 3 samples are treated as glitches. The widths within
    1.5x of the shortest are averaged, and the result snaps to the nearest
    standard rate when within ``snap_tolerance``.

    Args:
        signal: A digitized UART line with some traffic on it.
        snap_tolerance: Relative distance within which to snap.

    Returns:
        The estimated baud rate, or ``None`` if there are too few edges.
    """
    widths = np.diff(signal.edges).astype(float)
    widths = widths[widths >= 3]
    if widths.size == 0:
        return None
    shortest = widths.min()
    bit = widths[widths <= 1.5 * shortest].mean() * signal.dt
    raw = 1.0 / bit
    nearest = min(STANDARD_BAUD_RATES, key=lambda rate: abs(rate - raw))
    if abs(nearest - raw) / nearest <= snap_tolerance:
        return float(nearest)
    return raw


def _parity_bit(value: int, parity: str) -> int:
    """Expected parity bit for ``value``."""
    ones = value.bit_count() & 1
    return {"even": ones, "odd": ones ^ 1, "mark": 1, "space": 0}[parity]


def _format_value(value: int, display: str, data_bits: int) -> tuple[str, ...]:
    """Label variants for one character, longest first."""
    digits = (data_bits + 3) // 4
    hex_long, hex_short = f"0x{value:0{digits}X}", f"{value:0{digits}X}"
    if display == "dec":
        return (str(value),)
    if display == "ascii" and 32 <= value < 127:
        return (f"'{chr(value)}'", chr(value))
    return (hex_long, hex_short)


def _format_packet(values: list[int], display: str, data_bits: int) -> str:
    """One label for a run of characters."""
    if display == "ascii":
        return "".join(chr(v) if 32 <= v < 127 else f"\\x{v:02X}" for v in values)
    if display == "dec":
        return " ".join(str(v) for v in values)
    digits = (data_bits + 3) // 4
    return " ".join(f"{v:0{digits}X}" for v in values)


class UartDecoder(Decoder):
    """UART / RS-232 decoder for independent RX and TX lines."""

    # -- definition: class metadata the registry and GUI read ----------------
    id = "uart"
    name = "UART / RS-232"
    version = "1.0"
    description = "Asynchronous serial (8N1 and friends); invert for RS-232 levels."
    roles = (
        Role("rx", "RX", required=False, help="Receive line"),
        Role("tx", "TX", required=False, help="Transmit line"),
    )
    require_one_of = ("rx", "tx")
    options = (
        Option("baud", "Baud rate", int, 115200, help="Bits per second"),
        Option("data_bits", "Data bits", int, 8, choices=(5, 6, 7, 8, 9)),
        Option("parity", "Parity", str, "none", choices=("none", "even", "odd", "mark", "space")),
        Option("stop_bits", "Stop bits", float, 1.0, choices=(1.0, 1.5, 2.0)),
        Option("bit_order", "Bit order", str, "lsb", choices=("lsb", "msb")),
        Option("invert", "Invert (RS-232)", bool, False,
               help="RS-232 levels: idle is negative. Use a threshold near 0 V."),
        Option("packet_gap", "Packet gap (bits)", float, 0.0,
               help="Group characters separated by less than this many bit times; 0 = off"),
        Option("display", "Display", str, "hex", choices=("hex", "ascii", "dec")),
    )

    # -- logic ---------------------------------------------------------------

    def decode(self, signals: dict[str, LogicSignal], opts: dict[str, Any]) -> Iterator[Frame]:
        """Decode each mapped line independently."""
        if opts["baud"] <= 0:
            raise DecodeError("baud rate must be positive")
        for role in ("rx", "tx"):
            if role in signals:
                yield from self._decode_line(role, signals[role], opts)

    def _decode_line(self, role: str, sig: LogicSignal, opts: dict[str, Any]) -> Iterator[Frame]:
        bit = 1.0 / opts["baud"]
        nd: int = opts["data_bits"]
        np_bits = 0 if opts["parity"] == "none" else 1
        stop: float = opts["stop_bits"]
        frame_time = (1 + nd + np_bits + stop) * bit
        # Longest high run that can occur inside one character's data/parity.
        certain_idle = (nd + np_bits + 0.5) * bit

        if warning := oversampling_warning(sig, opts["baud"]):
            self.warn(f"{role}: {warning}")
        if opts["invert"]:
            sig = sig.inverted()

        # Sample points (in bit times from the start edge): start, data, parity, stop(s).
        offsets = [0.5] + [1.5 + i for i in range(nd + np_bits)] + [1.5 + nd + np_bits]
        if stop == 2.0:
            offsets.append(2.5 + nd + np_bits)
        offsets_arr = np.array(offsets)

        t = sig.t_start
        if not sig.level_at(t):
            # Capture opens mid-character or mid-break: wait for the line to go high.
            rise = sig.next_edge(t, "rising")
            if rise is None:
                return
            t = rise
        # Until one character decodes cleanly, a falling edge might be a data
        # bit of a character that began before the capture.
        synced = False
        idle_since = t
        packet: list[Frame] = []

        while (ts := sig.next_edge(t, "falling")) is not None:
            rise = sig.next_edge(ts, "rising")
            end_of_char = ts + frame_time

            # Break: line held low for longer than a whole character.
            if (rise is None and sig.t_end >= end_of_char) or (rise is not None and rise > end_of_char):
                yield from self._flush_packet(role, packet, opts)
                packet = []
                break_end = rise if rise is not None else sig.t_end
                yield self.frame(ts, break_end, "break", Level.WORD, data={"role": role},
                                 text=(f"{role.upper()}: Break", "Break", "BRK"))
                if rise is None:
                    return
                # The line is known idle now, so the next falling edge is a start bit.
                t = rise
                synced = True
                continue

            sample_times = ts + offsets_arr * bit
            if sample_times[-1] > sig.t_end:
                yield from self._truncated(role, sig, ts, sample_times, nd, bit, opts)
                break

            levels = sig.levels_at(sample_times).astype(int)
            if levels[0]:
                # Start bit is not low at its centre: a glitch, not a start.
                t = ts
                continue

            data_bits = levels[1:1 + nd]
            value = 0
            ordered = data_bits if opts["bit_order"] == "msb" else data_bits[::-1]
            for b in ordered:
                value = (value << 1) | int(b)
            parity_ok: bool | None = None
            if np_bits:
                parity_ok = int(levels[1 + nd]) == _parity_bit(value, opts["parity"])
            stop_ok = bool(levels[1 + nd + np_bits:].all())

            uncertain = False
            if not synced:
                rises = sig.edges_between(idle_since, ts, "rising")
                high_since = float(rises[-1]) if rises.size else idle_since
                uncertain = ts - high_since < certain_idle
                if uncertain and not (stop_ok and parity_ok is not False):
                    # Probably a data edge of a character cut off by the capture start.
                    t = ts
                    continue
                synced = True

            char_frames = self._character_frames(
                role, ts, bit, data_bits, value, parity_ok, stop_ok, stop, nd, np_bits, uncertain, opts
            )
            byte_frame = char_frames[-1]
            yield from char_frames

            if opts["packet_gap"] > 0:
                if packet and ts - packet[-1].end > opts["packet_gap"] * bit:
                    yield from self._flush_packet(role, packet, opts)
                    packet = []
                packet.append(byte_frame)

            t = ts + (1 + nd + np_bits + 0.5) * bit

        yield from self._flush_packet(role, packet, opts)

    def _character_frames(
        self,
        role: str,
        ts: float,
        bit: float,
        data_bits: np.ndarray,
        value: int,
        parity_ok: bool | None,
        stop_ok: bool,
        stop: float,
        nd: int,
        np_bits: int,
        uncertain: bool,
        opts: dict[str, Any],
    ) -> list[Frame]:
        """Bit-level frames plus the character frame (last in the list).

        ``uncertain`` marks a first character whose start edge could not be
        told apart from a data edge (the capture began mid-stream).
        """
        frames = [self.frame(ts, ts + bit, "start", Level.BIT, data={"role": role}, text=("Start", "S"))]
        for i, b in enumerate(data_bits):
            index = i if opts["bit_order"] == "lsb" else nd - 1 - i
            t_bit = ts + (1 + i) * bit
            frames.append(self.frame(t_bit, t_bit + bit, "bit", Level.BIT,
                                     data={"role": role, "index": index, "value": int(b)}, text=(str(int(b)),)))
        errors: list[str] = []
        if np_bits:
            t_par = ts + (1 + nd) * bit
            if parity_ok:
                frames.append(self.frame(t_par, t_par + bit, "parity", Level.BIT,
                                         data={"role": role}, text=("Parity", "P")))
            else:
                errors.append("parity error")
                frames.append(self.frame(t_par, t_par + bit, "parity_error", Level.BIT, data={"role": role},
                                         text=("Parity error", "PE", "!"), error="parity error"))
        t_stop = ts + (1 + nd + np_bits) * bit
        if stop_ok:
            frames.append(self.frame(t_stop, t_stop + stop * bit, "stop", Level.BIT,
                                     data={"role": role}, text=("Stop", "T")))
        else:
            errors.append("framing error")
            frames.append(self.frame(t_stop, t_stop + stop * bit, "framing_error", Level.BIT, data={"role": role},
                                     text=("Framing error", "FE", "!"), error="framing error"))

        labels = _format_value(value, opts["display"], nd)
        error = ", ".join(errors) or None
        long_label = f"{role.upper()}: {labels[0]}" + (f" ({error})" if error else "")
        data: dict[str, Any] = {"role": role, "value": value, "parity_ok": parity_ok, "stop_ok": stop_ok}
        if uncertain:
            long_label += " (sync uncertain)"
            data["uncertain"] = True
        frames.append(self.frame(
            ts, t_stop + stop * bit, "byte", Level.WORD,
            data=data,
            text=(long_label, *labels), error=error,
        ))
        return frames

    def _truncated(
        self,
        role: str,
        sig: LogicSignal,
        ts: float,
        sample_times: np.ndarray,
        nd: int,
        bit: float,
        opts: dict[str, Any],
    ) -> Iterator[Frame]:
        """Frames for a character cut off by the end of the capture."""
        inside = sample_times[sample_times <= sig.t_end]
        levels = sig.levels_at(inside).astype(int)
        if levels.size and levels[0]:
            return  # a glitch, not a start bit
        yield self.frame(ts, ts + bit, "start", Level.BIT, data={"role": role}, text=("Start", "S"))
        for i, b in enumerate(levels[1:1 + nd]):
            index = i if opts["bit_order"] == "lsb" else nd - 1 - i
            t_bit = ts + (1 + i) * bit
            yield self.frame(t_bit, t_bit + bit, "bit", Level.BIT,
                             data={"role": role, "index": index, "value": int(b)}, text=(str(int(b)),))
        yield self.frame(ts, sig.t_end, "truncated", Level.WORD, data={"role": role},
                         text=(f"{role.upper()}: capture ended mid-character", "Truncated", "..."),
                         error="capture ended mid-character")

    def _flush_packet(self, role: str, packet: list[Frame], opts: dict[str, Any]) -> Iterator[Frame]:
        """Emit a level-2 packet frame for grouped characters."""
        if not packet:
            return
        values = [f.data["value"] for f in packet]
        label = _format_packet(values, opts["display"], opts["data_bits"])
        bad = sum(1 for f in packet if f.error)
        error = f"{bad} character error(s)" if bad else None
        yield self.frame(
            packet[0].start, packet[-1].end, "packet", Level.PACKET,
            data={"role": role, "values": values},
            text=(f"{role.upper()}: {label}", label, f"[{len(values)}]"), error=error,
        )
