"""SPI decoder.

Data is sampled on one clock edge per bit: the rising edge in modes 0 and 3,
the falling edge in modes 1 and 2 (mode = CPOL * 2 + CPHA). A transaction is
one chip-select assertion. Without a CS line, transactions are split where
the clock pauses for longer than ``idle_gap`` clock periods.

MOSI and MISO are sampled at the same edges, so each word frame carries both.
Bits left over when CS de-asserts (or the capture ends) are flagged as an
incomplete word.

SPI has no formal standard; the mode numbering follows the common Motorola
CPOL/CPHA convention.
"""

__lazy_modules__ = ["numpy"]

from collections.abc import Iterator
from typing import Any

import numpy as np

from better_scope.decode.api import Decoder, DecodeError, Option, Role
from better_scope.decode.digitize import oversampling_warning
from better_scope.decode.model import Frame, Level, LogicSignal

MIN_WORD_SIZE = 4
MAX_WORD_SIZE = 32
_LINES = ("mosi", "miso")


class SpiDecoder(Decoder):
    """SPI, modes 0-3, 4-32 bit words, with or without chip select."""

    id = "spi"
    name = "SPI"
    version = "1.0"
    description = "Serial Peripheral Interface: CPOL/CPHA modes, MOSI/MISO words per CS assertion."
    roles = (
        Role("sclk", "SCLK", help="Serial clock"),
        Role("mosi", "MOSI", required=False, help="Controller out, target in"),
        Role("miso", "MISO", required=False, help="Controller in, target out"),
        Role("cs", "CS", required=False, help="Chip select; leave unmapped to split on clock idle gaps"),
    )
    require_one_of = ("mosi", "miso")
    options = (
        Option("mode", "Mode", int, 0, choices=(0, 1, 2, 3),
               help="0/3 sample on the rising edge, 1/2 on the falling edge"),
        Option("cs_polarity", "CS active", str, "low", choices=("low", "high")),
        Option("bit_order", "Bit order", str, "msb", choices=("msb", "lsb")),
        Option("word_size", "Word size (bits)", int, 8, help=f"{MIN_WORD_SIZE}-{MAX_WORD_SIZE}"),
        Option("idle_gap", "Idle gap (clocks)", float, 10.0,
               help="Without CS: a clock pause longer than this many periods ends a transaction"),
    )

    def decode(self, signals: dict[str, LogicSignal], opts: dict[str, Any]) -> Iterator[Frame]:
        """Decode words and transactions."""
        word_size: int = opts["word_size"]
        if not MIN_WORD_SIZE <= word_size <= MAX_WORD_SIZE:
            raise DecodeError(f"word size must be {MIN_WORD_SIZE}-{MAX_WORD_SIZE} bits, got {word_size}")
        sclk = signals["sclk"]
        t_end = min(sig.t_end for sig in signals.values())

        edges = sclk.edge_times
        rising = (np.arange(edges.size) % 2 == 0) != sclk.initial
        sample_rising = opts["mode"] in (0, 3)
        samples = edges[(rising == sample_rising) & (edges <= t_end)]
        if samples.size == 0:
            self.warn("no clock edges found")
            return
        period = float(np.median(np.diff(samples))) if samples.size > 1 else sclk.dt
        if samples.size > 1 and (warning := oversampling_warning(sclk, 1.0 / period, "clock")):
            self.warn(warning)

        levels = {line: signals[line].levels_at(samples) for line in _LINES if line in signals}
        if "cs" in signals:
            windows = self._cs_windows(signals["cs"], opts["cs_polarity"], t_end)
        else:
            windows = self._gap_windows(samples, period, opts["idle_gap"])
        for w_start, w_end, open_start, open_end in windows:
            lo = int(np.searchsorted(samples, w_start, side="left"))
            hi = int(np.searchsorted(samples, w_end, side="right"))
            yield from self._transaction(samples[lo:hi], {k: v[lo:hi] for k, v in levels.items()},
                                         w_start, w_end, open_start, open_end, period, opts)

    @staticmethod
    def _cs_windows(cs: LogicSignal, polarity: str, t_end: float) -> list[tuple[float, float, bool, bool]]:
        """``(start, end, open_start, open_end)`` per CS assertion."""
        active = cs if polarity == "high" else cs.inverted()
        edges = active.edge_times
        rising = (np.arange(edges.size) % 2 == 0) != active.initial
        starts = list(edges[rising])
        ends = list(edges[~rising])
        open_start = active.initial
        if open_start:
            starts.insert(0, active.t_start)
        open_end = len(ends) < len(starts)
        if open_end:
            ends.append(t_end)
        windows = []
        for index, (s, e) in enumerate(zip(starts, ends)):
            windows.append((float(s), float(e), open_start and index == 0, open_end and index == len(starts) - 1))
        return windows

    @staticmethod
    def _gap_windows(samples: np.ndarray, period: float, idle_gap: float) -> list[tuple[float, float, bool, bool]]:
        """Windows around runs of clock edges separated by less than ``idle_gap`` periods."""
        breaks = np.flatnonzero(np.diff(samples) > idle_gap * period) + 1
        bounds = np.concatenate([[0], breaks, [samples.size]])
        return [
            (float(samples[a]) - period / 2, float(samples[b - 1]) + period / 2, False, False)
            for a, b in zip(bounds[:-1], bounds[1:])
        ]

    def _transaction(
        self,
        times: np.ndarray,
        levels: dict[str, np.ndarray],
        w_start: float,
        w_end: float,
        open_start: bool,
        open_end: bool,
        period: float,
        opts: dict[str, Any],
    ) -> Iterator[Frame]:
        """Bit, word and transaction frames for one CS assertion / clock burst."""
        word_size: int = opts["word_size"]
        digits = (word_size + 3) // 4
        msb = opts["bit_order"] == "msb"
        n = times.size
        ends = np.append(times[1:], times[-1] + period) if n else times
        words: dict[str, list[int]] = {line: [] for line in levels}
        errors: list[str] = []

        for k in range(n):
            bit_data: dict[str, Any] = {"index": (word_size - 1 - k % word_size) if msb else k % word_size}
            bit_data.update({line: int(v[k]) for line, v in levels.items()})
            label = "/".join(str(bit_data[line]) for line in levels)
            yield self.frame(float(times[k]), float(ends[k]), "bit", Level.BIT, data=bit_data, text=(label,))

        n_words, leftover = divmod(n, word_size)
        for w in range(n_words):
            a, b = w * word_size, (w + 1) * word_size
            values: dict[str, int] = {}
            for line, v in levels.items():
                bits = v[a:b] if msb else v[a:b][::-1]
                value = 0
                for bit in bits:
                    value = (value << 1) | int(bit)
                values[line] = value
                words[line].append(value)
            label = " / ".join(f"{line.upper()} 0x{val:0{digits}X}" for line, val in values.items())
            short = "/".join(f"{val:0{digits}X}" for val in values.values())
            yield self.frame(float(times[a]), float(ends[b - 1]), "word", Level.WORD,
                             data={"mosi": values.get("mosi"), "miso": values.get("miso")},
                             text=(label, short))
        if leftover:
            a = n_words * word_size
            msg = f"incomplete word ({leftover} of {word_size} bits)"
            errors.append(msg)
            yield self.frame(float(times[a]), float(ends[-1]), "incomplete_word", Level.WORD,
                             data={line: [int(x) for x in v[a:]] for line, v in levels.items()},
                             text=(f"Incomplete word ({leftover} bits)", "Incomplete", "!"), error=msg)

        parts = [f"{line.upper()}: " + " ".join(f"{val:0{digits}X}" for val in vals) for line, vals in words.items()]
        label = " | ".join(parts) if n_words else "no complete words"
        yield self.frame(
            w_start, w_end, "transaction", Level.PACKET,
            data={
                "mosi": words.get("mosi"),
                "miso": words.get("miso"),
                "word_size": word_size,
                "started_before_capture": open_start,
                "ended_after_capture": open_end,
            },
            text=(label, f"[{n_words}]"),
            error="; ".join(errors) or None,
        )
