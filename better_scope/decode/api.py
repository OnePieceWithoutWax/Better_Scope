"""Decoder plugin API.

A protocol is a :class:`Decoder` subclass: its *definition* is declarative
class metadata (``id``, ``roles``, ``options``, ``stacks_on``) and its *logic*
is :meth:`Decoder.decode` (base decoders, fed logic signals) or
:meth:`Decoder.decode_frames` (stacked decoders, fed the frames of the layer
below). The GUI builds its configuration form from the metadata alone.

See ``docs/DECODERS.md`` for a walkthrough.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from better_scope.decode.model import Frame, LogicSignal

OPTION_TYPES: tuple[type, ...] = (int, float, bool, str)


class DecodeError(Exception):
    """Raised by a decoder for a configuration problem it cannot decode past.

    The engine turns it into a warning on the result; it never aborts other
    buses.
    """


@dataclass(frozen=True)
class Role:
    """A named input signal of a base decoder (e.g. ``rx``, ``scl``).

    Attributes:
        id: Key used in ``BusConfig.role_map`` and in ``decode(signals)``.
        label: Display name.
        required: The bus cannot run without it.
        help: Tooltip text.
    """

    id: str
    label: str
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class Option:
    """A user-settable decoder option.

    The type is one of ``int``, ``float``, ``bool`` or ``str``. A non-empty
    ``choices`` makes it a choice option (shown as a dropdown); the values
    keep their declared type.

    Attributes:
        id: Key in the options dict.
        label: Display name.
        type: Value type.
        default: Default value (must match ``type`` and ``choices``).
        choices: Allowed values, or empty for free entry.
        help: Tooltip text.
    """

    id: str
    label: str
    type: type
    default: Any
    choices: tuple[Any, ...] = ()
    help: str = ""

    def accepts(self, value: Any) -> bool:
        """Whether ``value`` is valid for this option without coercion."""
        if self.type is float:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif self.type is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, self.type)
        return ok and (not self.choices or value in self.choices)

    def coerce(self, value: Any) -> Any:
        """Convert ``value`` (e.g. from JSON or a text box) to this option's type.

        Raises:
            ValueError: If the value cannot be converted or is not a choice.
        """
        if self.type is bool and isinstance(value, str):
            lowered = value.strip().lower()
            if lowered not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                raise ValueError(f"option {self.id!r}: {value!r} is not a boolean")
            out: Any = lowered in ("true", "1", "yes", "on")
        else:
            try:
                out = self.type(value)
            except (TypeError, ValueError) as e:
                raise ValueError(f"option {self.id!r}: cannot convert {value!r} to {self.type.__name__}") from e
        if self.choices and out not in self.choices:
            raise ValueError(f"option {self.id!r}: {value!r} is not one of {list(self.choices)}")
        return out


class Decoder:
    """Base class for protocol decoders.

    Subclasses set the class metadata and override exactly one of
    :meth:`decode` (base decoder, ``stacks_on is None``) or
    :meth:`decode_frames` (stacked decoder). A decoder instance handles one
    bus for one run; build frames with :meth:`frame` and report non-fatal
    problems with :meth:`warn`.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    version: ClassVar[str] = "1.0"
    description: ClassVar[str] = ""
    roles: ClassVar[tuple[Role, ...]] = ()
    # At least one of these role ids must be mapped (e.g. UART rx/tx).
    require_one_of: ClassVar[tuple[str, ...]] = ()
    options: ClassVar[tuple[Option, ...]] = ()
    stacks_on: ClassVar[str | None] = None

    def __init__(self, bus_id: str = "") -> None:
        self.bus_id = bus_id
        self.warnings: list[str] = []

    @classmethod
    def default_options(cls) -> dict[str, Any]:
        """Option id -> default value."""
        return {opt.id: opt.default for opt in cls.options}

    def frame(
        self,
        start: float,
        end: float,
        kind: str,
        level: int,
        *,
        data: dict[str, Any] | None = None,
        text: Iterable[str] = (),
        error: str | None = None,
    ) -> Frame:
        """Build a :class:`Frame` tagged with this decoder and bus.

        Args:
            start: Start time in seconds.
            end: End time in seconds.
            kind: Frame type tag.
            level: Annotation level (see :class:`~better_scope.decode.model.Level`).
            data: Structured payload.
            text: Label variants, longest first.
            error: Error description for error frames.
        """
        return Frame(
            start=start,
            end=end,
            bus_id=self.bus_id,
            decoder_id=self.id,
            kind=kind,
            level=int(level),
            data=data or {},
            text=tuple(text),
            error=error,
        )

    def warn(self, message: str) -> None:
        """Record a non-fatal warning for this run."""
        self.warnings.append(message)

    def decode(self, signals: dict[str, LogicSignal], opts: dict[str, Any]) -> Iterator[Frame]:
        """Decode logic signals (base decoders).

        Args:
            signals: Role id -> signal, for every mapped role.
            opts: Every option of the stack, validated, with defaults filled in.

        Yields:
            Frames in any order.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement decode()")

    def decode_frames(self, frames: list[Frame], opts: dict[str, Any]) -> Iterator[Frame]:
        """Decode the frames of the layer below (stacked decoders).

        Args:
            frames: All frames produced by the ``stacks_on`` layer, sorted by
                start time.
            opts: Every option of the stack, validated, with defaults filled in.

        Yields:
            Frames in any order.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement decode_frames()")


def validate_decoder(cls: type[Decoder]) -> list[str]:
    """Check a decoder class's metadata.

    Args:
        cls: The decoder class.

    Returns:
        Problems found (empty when valid). ``stacks_on`` targets are checked
        by the registry once every decoder is known.
    """
    problems: list[str] = []
    for attr in ("id", "name"):
        value = getattr(cls, attr, None)
        if not isinstance(value, str) or not value:
            problems.append(f"missing or empty class attribute {attr!r}")

    role_ids = [r.id for r in cls.roles]
    if len(set(role_ids)) != len(role_ids):
        problems.append(f"duplicate role ids: {role_ids}")
    unknown = set(cls.require_one_of) - set(role_ids)
    if unknown:
        problems.append(f"require_one_of names unknown roles: {sorted(unknown)}")

    option_ids = [o.id for o in cls.options]
    if len(set(option_ids)) != len(option_ids):
        problems.append(f"duplicate option ids: {option_ids}")
    for opt in cls.options:
        if opt.type not in OPTION_TYPES:
            problems.append(f"option {opt.id!r}: unsupported type {opt.type!r}")
            continue
        bad_choices = [c for c in opt.choices if not Option(opt.id, "", opt.type, c).accepts(c)]
        if bad_choices:
            problems.append(f"option {opt.id!r}: choices {bad_choices} do not match type {opt.type.__name__}")
        elif not opt.accepts(opt.default):
            problems.append(f"option {opt.id!r}: default {opt.default!r} does not match its type/choices")

    overrides_decode = cls.decode is not Decoder.decode
    overrides_frames = cls.decode_frames is not Decoder.decode_frames
    if cls.stacks_on is None:
        if not overrides_decode:
            problems.append("base decoder must implement decode()")
        if not cls.roles:
            problems.append("base decoder must declare at least one role")
    elif not overrides_frames:
        problems.append("stacked decoder must implement decode_frames()")
    return problems
