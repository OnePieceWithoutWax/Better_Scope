"""Synthetic analog waveforms for decoder tests.

Signals are built as ideal logic segments, then given a linear edge time,
Gaussian noise and a DC offset, so decoders are exercised through the real
digitizer rather than on perfect logic.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def levels_waveform(
    segments: Sequence[tuple[bool, float]],
    sample_rate: float,
    *,
    v_low: float = 0.0,
    v_high: float = 3.3,
    edge_time: float = 0.0,
    noise: float = 0.0,
    offset: float = 0.0,
    t0: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Render logic segments as an analog waveform.

    Args:
        segments: ``(level, duration_seconds)`` pairs, in order.
        sample_rate: Samples per second.
        v_low: Voltage of logic 0.
        v_high: Voltage of logic 1.
        edge_time: 0-100% transition time in seconds (linear ramp).
        noise: Gaussian noise standard deviation as a fraction of the swing.
        offset: DC offset added to every sample, in volts.
        t0: Time of the first sample.
        seed: RNG seed.

    Returns:
        ``(t, v)`` arrays.
    """
    bounds = np.cumsum([0.0] + [d for _, d in segments])
    n = int(round(bounds[-1] * sample_rate))
    t_rel = np.arange(n) / sample_rate
    seg_index = np.clip(np.searchsorted(bounds, t_rel, side="right") - 1, 0, len(segments) - 1)
    logic = np.array([lv for lv, _ in segments], dtype=float)[seg_index]

    edge_samples = int(round(edge_time * sample_rate))
    if edge_samples > 1:
        kernel = np.ones(edge_samples) / edge_samples
        padded = np.concatenate([np.full(edge_samples, logic[0]), logic, np.full(edge_samples, logic[-1])])
        logic = np.convolve(padded, kernel, mode="same")[edge_samples:-edge_samples]

    swing = v_high - v_low
    v = v_low + logic * swing + offset
    if noise:
        v = v + np.random.default_rng(seed).normal(0.0, noise * abs(swing), n)
    return t0 + t_rel, v


@dataclass(frozen=True)
class Break:
    """A UART break: the line held low for ``bits`` bit times."""

    bits: float = 20.0


def uart_levels(
    items: Sequence[int | Break],
    *,
    data_bits: int = 8,
    parity: str = "none",
    stop_bits: float = 1.0,
    lsb_first: bool = True,
    idle_bits: float = 5.0,
    gap_bits: float = 0.0,
    parity_errors: Sequence[int] = (),
    framing_errors: Sequence[int] = (),
) -> list[tuple[bool, float]]:
    """UART logic segments in bit times (idle high), before inversion.

    Args:
        items: Character values, or :class:`Break` markers.
        data_bits: Data bits per character.
        parity: ``none``/``even``/``odd``/``mark``/``space``.
        stop_bits: 1, 1.5 or 2.
        lsb_first: Bit order.
        idle_bits: Idle time before the first and after the last character.
        gap_bits: Idle time between characters.
        parity_errors: Item indices whose parity bit is flipped.
        framing_errors: Item indices whose stop bit is driven low.

    Returns:
        ``(level, duration_in_bits)`` segments.
    """
    segs: list[tuple[bool, float]] = [(True, idle_bits)]
    for index, item in enumerate(items):
        if index and gap_bits:
            segs.append((True, gap_bits))
        if isinstance(item, Break):
            segs += [(False, item.bits), (True, 2.0)]
            continue
        bits = [(item >> i) & 1 for i in range(data_bits)]
        if not lsb_first:
            bits.reverse()
        segs.append((False, 1.0))
        segs += [(bool(b), 1.0) for b in bits]
        if parity != "none":
            ones = item.bit_count() & 1
            p = {"even": ones, "odd": ones ^ 1, "mark": 1, "space": 0}[parity]
            if index in parity_errors:
                p ^= 1
            segs.append((bool(p), 1.0))
        segs.append((index not in framing_errors, stop_bits))
    segs.append((True, idle_bits))
    return segs


def uart_waveform(
    items: Sequence[int | Break],
    baud: float,
    *,
    samples_per_bit: float = 16.0,
    invert: bool = False,
    v_low: float | None = None,
    v_high: float | None = None,
    edge_fraction: float = 0.1,
    noise: float = 0.02,
    offset: float = 0.0,
    seed: int = 0,
    **frame_kwargs: object,
) -> tuple[np.ndarray, np.ndarray]:
    """Analog UART waveform.

    Args:
        items: Character values, or :class:`Break` markers.
        baud: Bit rate.
        samples_per_bit: Oversampling ratio.
        invert: RS-232 levels (logic 1 = negative voltage). Defaults the
            voltages to +/-12 V instead of 0/3.3 V.
        v_low: Voltage of logic 0 (before inversion).
        v_high: Voltage of logic 1 (before inversion).
        edge_fraction: Edge time as a fraction of a bit.
        noise: Noise std-dev as a fraction of the swing.
        offset: DC offset in volts.
        seed: RNG seed.
        **frame_kwargs: Passed to :func:`uart_levels`.

    Returns:
        ``(t, v)`` arrays.
    """
    bit = 1.0 / baud
    segs = [(lv, d * bit) for lv, d in uart_levels(items, **frame_kwargs)]  # type: ignore[arg-type]
    if invert:
        v_low = 12.0 if v_low is None else v_low
        v_high = -12.0 if v_high is None else v_high
    else:
        v_low = 0.0 if v_low is None else v_low
        v_high = 3.3 if v_high is None else v_high
    return levels_waveform(
        segs, baud * samples_per_bit, v_low=v_low, v_high=v_high,
        edge_time=edge_fraction * bit, noise=noise, offset=offset, seed=seed,
    )


@dataclass(frozen=True)
class I2cSegment:
    """One addressed part of an I2C transaction (START/Sr to the next Sr/STOP).

    Attributes:
        address: 7-bit address, or a 10-bit address when ``ten_bit``.
        read: R/W bit.
        data: Bytes written by the controller or returned by the target.
        address_ack: Whether the target ACKs its address. On a NACK the
            controller sends STOP and later segments are dropped.
        nack_data: Indices of written bytes the target NACKs.
        ten_bit: Send a 10-bit address header (11110 + A9:A8).
    """

    address: int
    read: bool = False
    data: Sequence[int] = ()
    address_ack: bool = True
    nack_data: Sequence[int] = ()
    ten_bit: bool = False


def i2c_steps(
    transactions: Sequence[Sequence[I2cSegment]],
    *,
    idle_bits: float = 4.0,
    stretch_bits: float = 0.0,
) -> list[tuple[bool, bool, float]]:
    """I2C bus states as ``(scl, sda, duration_in_bits)`` steps.

    Each bit is four quarter-bit steps: SCL low holding the old SDA, SCL low
    with the new SDA, then SCL high for half a bit.

    Args:
        transactions: Transactions, each a list of segments joined by repeated STARTs.
        idle_bits: Bus-idle time before, between and after transactions.
        stretch_bits: Extra SCL-low time before every ACK clock (clock stretching).

    Returns:
        The steps.
    """
    q = 0.25
    steps: list[tuple[bool, bool, float]] = [(True, True, idle_bits)]
    sda = True

    def bit(value: int, stretch: float = 0.0) -> None:
        nonlocal sda
        steps.append((False, sda, q))
        sda = bool(value)
        steps.append((False, sda, q + stretch))
        steps.append((True, sda, 2 * q))

    def byte(value: int, ack: bool) -> None:
        for i in range(7, -1, -1):
            bit((value >> i) & 1)
        bit(0 if ack else 1, stretch_bits)

    for txn in transactions:
        # START: SDA falls while SCL is high.
        steps.append((True, False, q))
        sda = False
        for index, seg in enumerate(txn):
            if index:
                # Repeated START: release SDA with SCL low, raise SCL, pull SDA low.
                steps += [(False, sda, q), (False, True, q), (True, True, q), (True, False, q)]
                sda = False
            if seg.ten_bit:
                byte(0b11110000 | ((seg.address >> 7) & 0b110) | int(seg.read), seg.address_ack)
                byte(seg.address & 0xFF, seg.address_ack)
            else:
                byte((seg.address << 1) | int(seg.read), seg.address_ack)
            if not seg.address_ack:
                break
            for i, value in enumerate(seg.data):
                ack = i != len(seg.data) - 1 if seg.read else i not in seg.nack_data
                byte(value, ack)
        # STOP: SDA rises while SCL is high.
        steps += [(False, sda, q), (False, False, q), (True, False, q), (True, True, idle_bits)]
        sda = True
    return steps


def i2c_waveform(
    transactions: Sequence[Sequence[I2cSegment]],
    freq: float,
    *,
    samples_per_bit: float = 40.0,
    edge_fraction: float = 0.05,
    noise: float = 0.02,
    v_high: float = 3.3,
    seed: int = 0,
    **step_kwargs: float,
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """Analog SCL and SDA waveforms.

    Args:
        transactions: Transactions, each a list of :class:`I2cSegment`.
        freq: SCL frequency in Hz.
        samples_per_bit: Oversampling ratio.
        edge_fraction: Edge time as a fraction of a bit.
        noise: Noise std-dev as a fraction of the swing.
        v_high: Pull-up voltage.
        seed: RNG seed (SDA uses ``seed + 1``).
        **step_kwargs: Passed to :func:`i2c_steps`.

    Returns:
        ``((t, scl), (t, sda))``.
    """
    bit = 1.0 / freq
    steps = i2c_steps(transactions, **step_kwargs)
    common = dict(v_high=v_high, edge_time=edge_fraction * bit, noise=noise)
    scl = levels_waveform([(c, d * bit) for c, _, d in steps], freq * samples_per_bit, seed=seed, **common)
    sda = levels_waveform([(s, d * bit) for _, s, d in steps], freq * samples_per_bit, seed=seed + 1, **common)
    return scl, sda


def spi_waveform(
    transactions: Sequence[Sequence[tuple[int, int]]],
    freq: float,
    *,
    mode: int = 0,
    word_size: int = 8,
    lsb_first: bool = False,
    cs_active_low: bool = True,
    gap_clocks: float = 20.0,
    partial_bits: int = 0,
    samples_per_bit: float = 20.0,
    edge_fraction: float = 0.05,
    noise: float = 0.02,
    seed: int = 0,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Analog SPI waveforms.

    Args:
        transactions: Per CS assertion, a list of ``(mosi, miso)`` words.
        freq: SCLK frequency in Hz.
        mode: SPI mode 0-3 (CPOL * 2 + CPHA).
        word_size: Bits per word.
        lsb_first: Bit order.
        cs_active_low: CS polarity.
        gap_clocks: Idle time around and between transactions, in clocks.
        partial_bits: Extra clock cycles (zeros) at the end of the last
            transaction, making an incomplete word.
        samples_per_bit: Oversampling ratio.
        edge_fraction: Edge time as a fraction of a clock period.
        noise: Noise std-dev as a fraction of the swing.
        seed: RNG seed (each line offsets it).

    Returns:
        ``{"sclk": (t, v), "mosi": ..., "miso": ..., "cs": ...}``.
    """
    cpol, cpha = bool(mode & 2), bool(mode & 1)
    h = 0.5
    cs_on = not cs_active_low
    # Steps: (sclk, mosi, miso, cs, duration_in_clocks)
    steps: list[tuple[bool, bool, bool, bool, float]] = [(cpol, False, False, not cs_on, gap_clocks)]
    for index, words in enumerate(transactions):
        bits: list[tuple[int, int]] = []
        for mosi, miso in words:
            order = range(word_size) if lsb_first else range(word_size - 1, -1, -1)
            bits += [((mosi >> i) & 1, (miso >> i) & 1) for i in order]
        if index == len(transactions) - 1:
            bits += [(0, 0)] * partial_bits
        steps.append((cpol, False, False, cs_on, h))
        for mo, mi in bits:
            if cpha:
                # Data changes on the leading edge, sampled on the trailing edge.
                steps += [(not cpol, bool(mo), bool(mi), cs_on, h), (cpol, bool(mo), bool(mi), cs_on, h)]
            else:
                # Data set up in the idle half, sampled on the leading edge.
                steps += [(cpol, bool(mo), bool(mi), cs_on, h), (not cpol, bool(mo), bool(mi), cs_on, h)]
        steps.append((cpol, False, False, cs_on, h))
        steps.append((cpol, False, False, not cs_on, gap_clocks))

    period = 1.0 / freq
    rate = freq * samples_per_bit
    common = dict(edge_time=edge_fraction * period, noise=noise)
    return {
        name: levels_waveform([(s[i], s[4] * period) for s in steps], rate, seed=seed + i, **common)
        for i, name in enumerate(("sclk", "mosi", "miso", "cs"))
    }
