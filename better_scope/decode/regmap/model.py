"""Register-map data model: devices, registers, bit fields.

Format-agnostic: every importer (Excel today, more in later versions) builds
these objects. :func:`decode_value` splits a raw register value into its
field values. There is no register state tracking -- each access is decoded
on its own.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

BUS_TYPES: tuple[str, ...] = ("smbus", "pmbus", "spi")


@dataclass(frozen=True)
class RegmapIssue:
    """One problem found while loading a register map.

    Attributes:
        message: What is wrong.
        sheet: Sheet (or section) name, if known.
        row: 1-based row number, if known.
        column: Column letter and header, e.g. ``"G (Bits)"``, if known.
    """

    message: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None

    def __str__(self) -> str:
        where = [part for part in (
            f"sheet {self.sheet!r}" if self.sheet else "",
            f"row {self.row}" if self.row is not None else "",
            f"column {self.column}" if self.column else "",
        ) if part]
        return f"{', '.join(where)}: {self.message}" if where else self.message


class RegmapError(ValueError):
    """A register map failed to load; ``issues`` lists every problem found."""

    def __init__(self, source: str, issues: Iterable[RegmapIssue]) -> None:
        self.source = source
        self.issues = list(issues)
        lines = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"{source}: {len(self.issues)} problem(s)\n{lines}")


@dataclass(frozen=True)
class Field:
    """A bit field of a register.

    Attributes:
        name: Field name.
        lsb: Lowest bit position.
        width: Number of bits.
        description: Free text.
        enum: Field value -> label (e.g. ``{0: "Off", 1: "On"}``).
    """

    name: str
    lsb: int
    width: int
    description: str = ""
    enum: dict[int, str] = field(default_factory=dict)

    @property
    def msb(self) -> int:
        """Highest bit position."""
        return self.lsb + self.width - 1

    @property
    def mask(self) -> int:
        """Mask of this field's bits within the register."""
        return ((1 << self.width) - 1) << self.lsb

    @property
    def bits(self) -> str:
        """Bit range as text, ``"7:4"`` or ``"3"``."""
        return f"{self.msb}:{self.lsb}" if self.width > 1 else str(self.lsb)


@dataclass(frozen=True)
class Register:
    """A register at a fixed address.

    Attributes:
        name: Register name.
        address: Register address (SMBus/PMBus: the command code).
        width: Register width in bits.
        access: Access type (``RW``, ``RO``, ``WO``, ``W1C`` ...); informational.
        reset: Reset value, if known.
        description: Free text.
        fields: Bit fields, highest first.
    """

    name: str
    address: int
    width: int
    access: str = ""
    reset: int | None = None
    description: str = ""
    fields: tuple[Field, ...] = ()


@dataclass
class Device:
    """A device's register map.

    Attributes:
        name: Device name.
        bus: Bus type, one of :data:`BUS_TYPES`.
        address: Default 7-bit bus address (SMBus/PMBus), if any.
        addr_width: Register-address width in bits.
        data_width: Default register width in bits.
        registers: Register address -> :class:`Register`.
        source: Where the map was loaded from.
    """

    name: str
    bus: str = "smbus"
    address: int | None = None
    addr_width: int = 8
    data_width: int = 8
    registers: dict[int, Register] = field(default_factory=dict)
    source: str = ""

    def register(self, address: int) -> Register | None:
        """The register at ``address``, or ``None``."""
        return self.registers.get(address)

    def by_name(self, name: str) -> Register | None:
        """The register called ``name`` (case-insensitive), or ``None``."""
        wanted = name.casefold()
        return next((r for r in self.registers.values() if r.name.casefold() == wanted), None)


@dataclass(frozen=True)
class FieldValue:
    """One field of a decoded register value.

    Attributes:
        name: Field name, or ``RESERVED[msb:lsb]`` for set bits no field covers.
        msb: Highest bit position.
        lsb: Lowest bit position.
        value: Raw field value.
        label: Enum label for ``value``, if the field has one.
        reserved: True for uncovered bits.
    """

    name: str
    msb: int
    lsb: int
    value: int
    label: str | None = None
    reserved: bool = False

    @property
    def width(self) -> int:
        """Number of bits."""
        return self.msb - self.lsb + 1

    @property
    def text(self) -> str:
        """``NAME=value``: the enum label, decimal for 1 bit, hex otherwise."""
        if self.label is not None:
            shown = self.label
        elif self.width == 1:
            shown = str(self.value)
        else:
            shown = f"0x{self.value:0{(self.width + 3) // 4}X}"
        return f"{self.name}={shown}"


def decode_value(register: Register, raw: int) -> list[FieldValue]:
    """Split a raw register value into its fields, highest bit first.

    Runs of bits not covered by any field are reported as
    ``RESERVED[msb:lsb]`` when non-zero. A register without fields decodes
    to an empty list (show the raw value). Bits above the register width are
    treated as uncovered.

    Args:
        register: The register definition.
        raw: The raw value.

    Returns:
        Field values, ordered by descending bit position.
    """
    if not register.fields:
        return []
    out: list[FieldValue] = []
    covered = 0
    for f in register.fields:
        value = (raw & f.mask) >> f.lsb
        out.append(FieldValue(f.name, f.msb, f.lsb, value, f.enum.get(value)))
        covered |= f.mask
    top = max(register.width, raw.bit_length())
    bit = 0
    while bit < top:
        if covered >> bit & 1:
            bit += 1
            continue
        lsb = bit
        while bit < top and not covered >> bit & 1:
            bit += 1
        value = (raw >> lsb) & ((1 << (bit - lsb)) - 1)
        if value:
            msb = bit - 1
            name = f"RESERVED[{msb}:{lsb}]" if msb != lsb else f"RESERVED[{lsb}]"
            out.append(FieldValue(name, msb, lsb, value, reserved=True))
    out.sort(key=lambda fv: fv.msb, reverse=True)
    return out
