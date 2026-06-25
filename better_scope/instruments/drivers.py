"""Map a discovered instrument to its pymeasure driver class.

The pymeasure fork already provides full-featured Tektronix MSO drivers, so
there is no wrapper layer here -- ``BetterScope`` holds the pymeasure instrument
directly. This module only resolves which driver class to instantiate.
"""

from typing import Any

# Exact ``*IDN?`` model number -> pymeasure driver class name.
_MODEL_DRIVER_NAMES: dict[str, str] = {
    "MSO44": "MSO44",
    "MSO54": "MSO54",
    "MSO58": "MSO58",
}


def driver_for(instr_info: dict[str, Any]) -> type | None:
    """Return the pymeasure driver class for a discovered instrument.

    Args:
        instr_info: Instrument-info dict from
            :func:`better_scope.instruments.discovery.find_instruments`.

    Returns:
        A pymeasure ``Instrument`` subclass, or ``None`` if unsupported.
    """
    manufacturer = (instr_info.get("manufacturer") or "").upper()
    model_num = (instr_info.get("model_num") or "").upper()

    if "TEKTRONIX" not in manufacturer or not model_num:
        return None

    import pymeasure.instruments.tektronix as tek

    driver_name = _MODEL_DRIVER_NAMES.get(model_num)
    if driver_name and hasattr(tek, driver_name):
        return getattr(tek, driver_name)

    # Fall back to the common base scope for other Tektronix MSO 4/5/6 series.
    # The base class is not re-exported at the package level, so import it from
    # its defining module.
    if model_num.startswith("MSO"):
        from pymeasure.instruments.tektronix.mso.tektronix_common_base_scope import (
            TektronixBaseScope,
        )

        return TektronixBaseScope

    return None
