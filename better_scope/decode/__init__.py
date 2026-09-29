"""Software serial-bus decoding (GUI-agnostic; usable from scripts and notebooks).

Typical use::

    from better_scope.decode import BusConfig, run

    bus = BusConfig("dbg", "Debug UART", "uart", role_map={"rx": "CH2"}, options={"baud": 9600})
    result = run([bus], scope.acquire_waveforms(["CH2"]))
"""

from better_scope.decode.api import Decoder, DecodeError, Option, Role
from better_scope.decode.digitize import digitize
from better_scope.decode.engine import run
from better_scope.decode.export import frames_to_csv
from better_scope.decode.model import BusConfig, DecodeResult, Frame, Level, LogicSignal, Threshold
from better_scope.decode.registry import decoder_registry

__all__ = [
    "BusConfig",
    "DecodeError",
    "DecodeResult",
    "Decoder",
    "Frame",
    "Level",
    "LogicSignal",
    "Option",
    "Role",
    "Threshold",
    "decoder_registry",
    "digitize",
    "frames_to_csv",
    "run",
]
