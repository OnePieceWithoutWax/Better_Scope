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
