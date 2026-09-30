"""Digital (logic probe) sources on Tektronix MSO 4/5/6 FlexChannels.

A FlexChannel fitted with a TLP058 logic probe reports ``DIGITAL`` from
``CH<x>:PROBETYPE?`` and provides eight bits, named ``CH<x>_D0`` ..
``CH<x>_D7``. ``DATa:SOUrce CH<x>_DALL`` transfers all eight in one
``CURVe?``: one unsigned integer per sample, bit ``n`` = ``D<n>``.
:func:`split_bits` turns those integers into one boolean array per bit.

Source names and the DALL layout follow the 4/5/6 Series MSO programmer
manual (Tektronix 077-1305-xx); the bit order has still to be confirmed on a
TLP058. GUI-agnostic. ``core`` imports this module at startup, so numpy is
imported inside the functions that need it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

BITS_PER_PROBE = 8

# Optional "FILE:" (or any other) prefix, then CH<x>_D<n>.
_SOURCE_RE = re.compile(r"^(?:.*:)?CH(\d+)_D(\d+)$", re.IGNORECASE)

# WFMOutpre:BYT_Nr values the scope can report.
_UNSIGNED_WIDTHS = (1, 2, 4, 8)


def digital_source(channel: int, bit: int) -> str:
    """Source name of one digital bit, e.g. ``CH1_D0``."""
    return f"CH{channel}_D{bit}"


def digital_sources(channel: int) -> list[str]:
    """All bit source names of one FlexChannel's logic probe."""
    return [digital_source(channel, bit) for bit in range(BITS_PER_PROBE)]


def parse_digital_source(name: str) -> tuple[int, int] | None:
    """``(channel, bit)`` of a digital source name, or ``None`` if analog.

    A prefix such as ``FILE:`` is ignored, so loaded sources match too.
    """
    match = _SOURCE_RE.match(name.strip())
    if match is None:
        return None
    channel, bit = int(match.group(1)), int(match.group(2))
    return (channel, bit) if bit < BITS_PER_PROBE else None


def is_digital_source(name: str) -> bool:
    """Whether ``name`` is a digital bit source (``CH1_D3``, ``FILE:CH1_D3``)."""
    return parse_digital_source(name) is not None


def is_digital_probe(probe_type: object) -> bool:
    """Whether a ``CH<x>:PROBETYPE?`` reply means a logic probe."""
    return probe_type is not None and "DIG" in str(probe_type).strip().strip('"').upper()


def curve_to_ints(payload: bytes, byte_width: int, byte_order: str) -> np.ndarray:
    """Interpret a ``CURVe?`` binary payload as unsigned integers.

    The bits are what matter, so signed (RI) and unsigned (RP) data read the
    same way.

    Args:
        payload: The data bytes (block header already removed).
        byte_width: ``WFMOutpre:BYT_Nr``.
        byte_order: ``WFMOutpre:BYT_Or`` (``LSB`` or ``MSB``).

    Returns:
        One unsigned integer per sample.

    Raises:
        ValueError: For an unexpected width or a partial last sample.
    """
    import numpy as np

    if int(byte_width) not in _UNSIGNED_WIDTHS:
        raise ValueError(f"unsupported digital sample width {byte_width!r} bytes")
    endian = ">" if str(byte_order).strip().upper().startswith("MSB") else "<"
    if len(payload) % int(byte_width):
        raise ValueError(f"{len(payload)}-byte curve is not a whole number of {byte_width}-byte samples")
    return np.frombuffer(payload, dtype=f"{endian}u{int(byte_width)}")


def split_bits(values: np.ndarray, n_bits: int = BITS_PER_PROBE) -> list[np.ndarray]:
    """Split per-sample integers into one boolean array per bit.

    Args:
        values: Integer samples (any width); bit ``n`` becomes entry ``n``.
        n_bits: Number of bits to extract.

    Returns:
        ``n_bits`` boolean arrays, D0 first.
    """
    import numpy as np

    ints = np.asarray(values).astype(np.uint64, copy=False)
    return [((ints >> np.uint64(bit)) & np.uint64(1)).astype(bool) for bit in range(n_bits)]
