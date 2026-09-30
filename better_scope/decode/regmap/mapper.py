"""Map decoded bus transactions to register reads and writes.

Input is the level-2 ``transaction`` frames of the SMBus and SPI decoders
plus :class:`DeviceBinding` records saying which register map belongs to
which bus (and SMBus address). Output is a command history: one
:class:`RegisterAccess` per access, in time order, labelled with register
and field names. No register state is tracked -- each access stands alone.

SMBus/PMBus: device = 7-bit address, register = command code, value = the
data bytes, little-endian (SMBus 3.3.1 section 6.5.4). Without a user map
(or for codes a ``pmbus`` map does not define) the PMBus standard command
table gives the register name.

SPI: the register address, R/W flag and data sit at fixed bit positions of
a fixed-size frame, set per binding by :class:`SpiLayout`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from better_scope.decode.decoders.pmbus_commands import (
    COMMANDS,
    EXTENDED_COMMANDS,
    MFR_SPECIFIC_CODES,
    command_name,
)
from better_scope.decode.model import DecodeResult, Frame, Level
from better_scope.decode.regmap.model import Device, FieldValue, Register, decode_value

DECODER_ID = "regmap"

Direction = Literal["read", "write"]

_SMBUS_WRITES = {"send_byte", "write_byte", "write_word", "write_32", "write_64", "block_write"}
_SMBUS_READS = {"receive_byte", "read_byte", "read_word", "read_32", "read_64", "block_read"}
_SMBUS_CALLS = {"process_call", "block_process_call"}


@dataclass(frozen=True)
class SpiLayout:
    """Where the R/W flag, address and data sit in an SPI register frame.

    Bit positions count from the frame's least-significant bit (the last bit
    shifted out when MSB first). The default is the common 16-bit frame
    ``[R/W:1][ADDR:7][DATA:8]`` with R/W = 1 meaning read.

    Attributes:
        frame_bits: Bits per register access. A CS transaction holding a
            multiple of this is split into consecutive accesses.
        rw_bit: Position of the R/W flag, or ``None`` if the frame has none.
        read_value: R/W flag value that means read (0 or 1).
        addr_lsb: Position of the address LSB.
        addr_bits: Address width.
        data_lsb: Position of the data LSB.
        data_bits: Data width.
        read_line: Line carrying the data on reads, ``"miso"`` or ``"mosi"``.
        default_direction: Direction used when ``rw_bit`` is ``None``.
    """

    frame_bits: int = 16
    rw_bit: int | None = 15
    read_value: int = 1
    addr_lsb: int = 8
    addr_bits: int = 7
    data_lsb: int = 0
    data_bits: int = 8
    read_line: Literal["miso", "mosi"] = "miso"
    default_direction: Direction = "write"

    def __post_init__(self) -> None:
        spans = [("address", self.addr_lsb, self.addr_bits), ("data", self.data_lsb, self.data_bits)]
        if self.rw_bit is not None:
            spans.append(("rw_bit", self.rw_bit, 1))
        for name, lsb, width in spans:
            if width < 1 or lsb < 0 or lsb + width > self.frame_bits:
                raise ValueError(f"SPI layout: {name} bits [{lsb + width - 1}:{lsb}] do not fit a "
                                 f"{self.frame_bits}-bit frame")
        if self.read_value not in (0, 1):
            raise ValueError("SPI layout: read_value must be 0 or 1")
        if self.read_line not in ("miso", "mosi"):
            raise ValueError("SPI layout: read_line must be 'miso' or 'mosi'")

    @staticmethod
    def _bits(frame: int, lsb: int, width: int) -> int:
        return (frame >> lsb) & ((1 << width) - 1)

    def direction(self, mosi_frame: int) -> Direction:
        """Read or write, from the R/W flag of the MOSI frame."""
        if self.rw_bit is None:
            return self.default_direction
        return "read" if self._bits(mosi_frame, self.rw_bit, 1) == self.read_value else "write"

    def address(self, mosi_frame: int) -> int:
        """Register address from the MOSI frame."""
        return self._bits(mosi_frame, self.addr_lsb, self.addr_bits)

    def data(self, frame: int) -> int:
        """Data field of a frame."""
        return self._bits(frame, self.data_lsb, self.data_bits)


@dataclass(frozen=True)
class DeviceBinding:
    """Attach a register map to a decoded bus.

    Attributes:
        bus_id: Bus the device sits on (``BusConfig.bus_id``).
        device: The register map. ``None`` means the PMBus standard command
            table only (name, no fields).
        address: SMBus 7-bit address; ``None`` uses ``device.address``, and
            if that is also ``None`` the binding matches every address.
        spi_layout: Frame layout for SPI buses.
    """

    bus_id: str
    device: Device | None = None
    address: int | None = None
    spi_layout: SpiLayout = field(default_factory=SpiLayout)

    @property
    def smbus_address(self) -> int | None:
        """The address this binding matches (``None`` = any)."""
        if self.address is not None:
            return self.address
        return self.device.address if self.device is not None else None

    @property
    def device_name(self) -> str:
        """Display name of the device."""
        return self.device.name if self.device is not None else "PMBus"


@dataclass(frozen=True)
class RegisterAccess:
    """One register read or write.

    Attributes:
        start: Start time in seconds.
        end: End time in seconds.
        bus_id: Bus it was decoded on.
        device: Device name.
        device_address: SMBus 7-bit address (``None`` on SPI).
        direction: ``"read"`` or ``"write"``.
        register_address: Register address / command code, if known.
        value: Raw value, or ``None`` when the access carried no data.
        data: The data bytes (SMBus) or data words (SPI) the value came from.
        register: The register definition, or ``None`` if unknown.
        fields: Decoded field values.
        errors: Problems: NACK, PEC mismatch, unknown register, size mismatch.
    """

    start: float
    end: float
    bus_id: str
    device: str
    device_address: int | None
    direction: Direction
    register_address: int | None
    value: int | None
    data: tuple[int, ...] = ()
    register: Register | None = None
    fields: tuple[FieldValue, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def register_name(self) -> str:
        """Register name, or its hex address, or ``"?"``."""
        if self.register is not None:
            return self.register.name
        return f"0x{self.register_address:02X}" if self.register_address is not None else "?"

    @property
    def value_text(self) -> str:
        """Value as zero-padded hex (register width), or empty."""
        if self.value is None:
            return ""
        bits = self.register.width if self.register is not None and self.register.width else 8 * max(len(self.data), 1)
        return f"0x{self.value:0{max((bits + 3) // 4, 2)}X}"

    @property
    def labels(self) -> tuple[str, ...]:
        """Label variants, longest first: ``WR VOUT_CFG = 0xCC : EN=1, MODE=Auto``."""
        op = "RD" if self.direction == "read" else "WR"
        head = f"{op} {self.register_name}"
        with_value = f"{head} = {self.value_text}" if self.value is not None else head
        variants = [with_value]
        if self.fields:
            variants.insert(0, f"{with_value} : " + ", ".join(fv.text for fv in self.fields))
        if with_value != head:
            variants.append(head)
        variants.append(self.register_name)
        return tuple(variants)

    def to_frame(self) -> Frame:
        """A level-3 frame for the plot and event table."""
        return Frame(
            start=self.start,
            end=self.end,
            bus_id=self.bus_id,
            decoder_id=DECODER_ID,
            kind="register_access",
            level=int(Level.SEMANTIC),
            data={
                "device": self.device,
                "device_address": self.device_address,
                "direction": self.direction,
                "register_address": self.register_address,
                "register": self.register.name if self.register is not None else None,
                "value": self.value,
                "data": list(self.data),
                "fields": [
                    {"name": fv.name, "msb": fv.msb, "lsb": fv.lsb, "value": fv.value, "label": fv.label}
                    for fv in self.fields
                ],
            },
            text=self.labels,
            error="; ".join(self.errors) or None,
        )


def _pmbus_register(code: int) -> Register | None:
    """A name-only register for a PMBus standard/MFR/extended code (``None`` if reserved)."""
    if code in COMMANDS:
        size = COMMANDS[code].data_bytes
        return Register(COMMANDS[code].name, code, 8 * size if size else 0)
    if code in MFR_SPECIFIC_CODES or code in EXTENDED_COMMANDS:
        return Register(command_name(code), code, 0)
    return None


def _lookup(binding: DeviceBinding, reg_address: int) -> tuple[Register | None, str | None]:
    """Register for an address on a binding, and an error if unknown."""
    device = binding.device
    register = device.register(reg_address) if device is not None else None
    if register is None and (device is None or device.bus == "pmbus"):
        register = _pmbus_register(reg_address)
    if register is None:
        return None, f"unknown register 0x{reg_address:02X}"
    return register, None


def _field_values(register: Register | None, value: int | None) -> tuple[FieldValue, ...]:
    if register is None or value is None:
        return ()
    return tuple(decode_value(register, value))


def _size_error(register: Register | None, n_bits: int) -> str | None:
    if register is None or not register.width or n_bits == 0 or n_bits == register.width:
        return None
    return f"size mismatch: {register.name} is {register.width} bits, got {n_bits}"


def _map_smbus(frame: Frame, binding: DeviceBinding) -> list[RegisterAccess]:
    d = frame.data
    kind: str = d["type"]
    errors = [e for e in (frame.error or "").split("; ") if e]
    base = dict(start=frame.start, end=frame.end, bus_id=frame.bus_id, device=binding.device_name,
                device_address=d["address"])

    if kind in ("quick_command", "address_nack", "incomplete", "unknown"):
        if kind == "address_nack" and not any("NACK" in e for e in errors):
            errors.insert(0, "address NACK")
        reg_address = d["command"]
        register = _lookup(binding, reg_address)[0] if reg_address is not None else None
        return [RegisterAccess(**base, direction="read" if d["read"] else "write", register_address=reg_address,
                               value=None, register=register, errors=tuple(errors or [kind.replace("_", " ")]))]

    if kind == "receive_byte":
        data = tuple(d["read_data"])
        return [RegisterAccess(**base, direction="read", register_address=None,
                               value=int.from_bytes(bytes(data), "little") if data else None, data=data,
                               errors=tuple(errors))]

    if kind == "send_byte":
        # A data-less command (PMBus CLEAR_FAULTS, a pointer write...).
        reg_address, pieces = d["write_data"][0], [("write", ())]
    elif kind in _SMBUS_WRITES:
        reg_address, pieces = d["command"], [("write", tuple(d["write_data"]))]
    elif kind in _SMBUS_READS:
        reg_address, pieces = d["command"], [("read", tuple(d["read_data"]))]
    elif kind in _SMBUS_CALLS:
        reg_address = d["command"]
        pieces = [("write", tuple(d["write_data"])), ("read", tuple(d["read_data"]))]
    else:
        return []

    register, unknown = _lookup(binding, reg_address)
    out: list[RegisterAccess] = []
    for direction, data in pieces:
        errs = list(errors)
        if unknown:
            errs.append(unknown)
        value = int.from_bytes(bytes(data), "little") if data else None
        is_block = kind in ("block_write", "block_read", "block_process_call")
        if data and not is_block and (size_error := _size_error(register, 8 * len(data))):
            errs.append(size_error)
        out.append(RegisterAccess(**base, direction=direction, register_address=reg_address, value=value,
                                  data=data, register=register, fields=_field_values(register, value),
                                  errors=tuple(errs)))
    return out


def _join_words(words: list[int], word_size: int) -> int:
    value = 0
    for word in words:
        value = (value << word_size) | word
    return value


def _map_spi(frame: Frame, binding: DeviceBinding) -> list[RegisterAccess]:
    d = frame.data
    layout = binding.spi_layout
    word_size: int = d["word_size"]
    mosi: list[int] | None = d["mosi"]
    miso: list[int] | None = d["miso"]
    lines = {"mosi": mosi, "miso": miso}
    base = dict(bus_id=frame.bus_id, device=binding.device_name, device_address=None)
    errors = [e for e in (frame.error or "").split("; ") if e]

    if mosi is None:
        return [RegisterAccess(start=frame.start, end=frame.end, **base, direction=layout.default_direction,
                               register_address=None, value=None,
                               errors=(*errors, "MOSI is not mapped; cannot read the register address"))]
    total = len(mosi) * word_size
    if total == 0:
        return []
    if total % layout.frame_bits:
        return [RegisterAccess(start=frame.start, end=frame.end, **base, direction=layout.default_direction,
                               register_address=None, value=None, data=tuple(mosi),
                               errors=(*errors, f"transaction has {total} bits; layout expects multiples "
                                                f"of {layout.frame_bits}"))]

    n = total // layout.frame_bits
    duration = (frame.end - frame.start) / n
    joined = {line: _join_words(words, word_size) if words is not None else None for line, words in lines.items()}
    out: list[RegisterAccess] = []
    for k in range(n):
        shift = (n - 1 - k) * layout.frame_bits
        chunk = {line: (value >> shift) & ((1 << layout.frame_bits) - 1) if value is not None else None
                 for line, value in joined.items()}
        mosi_frame = chunk["mosi"]
        direction = layout.direction(mosi_frame)
        reg_address = layout.address(mosi_frame)
        register, unknown = _lookup(binding, reg_address)
        errs = list(errors)
        if unknown:
            errs.append(unknown)
        source = chunk[layout.read_line] if direction == "read" else mosi_frame
        value: int | None = None
        if source is None:
            errs.append(f"{layout.read_line.upper()} is not mapped; no read data")
        else:
            value = layout.data(source)
            if size_error := _size_error(register, layout.data_bits):
                errs.append(size_error)
        out.append(RegisterAccess(
            start=frame.start + k * duration, end=frame.start + (k + 1) * duration, **base,
            direction=direction, register_address=reg_address, value=value,
            data=(value,) if value is not None else (), register=register,
            fields=_field_values(register, value), errors=tuple(errs),
        ))
    return out


def _smbus_binding(bindings: list[DeviceBinding], address: int | None) -> DeviceBinding | None:
    exact = next((b for b in bindings if b.smbus_address is not None and b.smbus_address == address), None)
    return exact or next((b for b in bindings if b.smbus_address is None), None)


def map_frames(
    frames: Iterable[Frame], bindings: Iterable[DeviceBinding], *, auto_pmbus: bool = True
) -> list[RegisterAccess]:
    """Build the command history from decoded frames.

    Args:
        frames: Decoded frames of any levels and buses (only SMBus and SPI
            level-2 ``transaction`` frames are used).
        bindings: Register maps per bus (several per SMBus bus, one per
            address; the first binding of an SPI bus is used).
        auto_pmbus: Label buses that were decoded as PMBus but have no
            binding from the PMBus standard command table.

    Returns:
        Register accesses sorted by start time.
    """
    frames = list(frames)
    by_bus: dict[str, list[DeviceBinding]] = {}
    for binding in bindings:
        by_bus.setdefault(binding.bus_id, []).append(binding)
    if auto_pmbus:
        for f in frames:
            if f.decoder_id == "pmbus" and f.bus_id not in by_bus:
                by_bus[f.bus_id] = [DeviceBinding(f.bus_id)]

    out: list[RegisterAccess] = []
    for f in frames:
        if f.kind != "transaction" or f.level != Level.PACKET or f.bus_id not in by_bus:
            continue
        if f.decoder_id == "smbus":
            binding = _smbus_binding(by_bus[f.bus_id], f.data["address"])
            if binding is not None:
                out.extend(_map_smbus(f, binding))
        elif f.decoder_id == "spi":
            out.extend(_map_spi(f, by_bus[f.bus_id][0]))
    out.sort(key=lambda a: a.start)
    return out


def annotate(
    result: DecodeResult,
    bindings: Iterable[DeviceBinding],
    *,
    auto_pmbus: bool = True,
    replace_semantic: bool = True,
) -> list[RegisterAccess]:
    """Map a decode result and add the register frames to it (level 3).

    Args:
        result: Decode result; gains one ``regmap`` frame per access.
        bindings: Register maps per bus.
        auto_pmbus: See :func:`map_frames`.
        replace_semantic: Drop other level-3 frames (e.g. PMBus command
            names) that cover exactly the same span as a register access;
            the register frame carries the same name plus the fields.

    Returns:
        The register accesses, sorted by start time.
    """
    accesses = map_frames(result.iter_frames(), bindings, auto_pmbus=auto_pmbus)
    if replace_semantic:
        covered: dict[str, set[tuple[float, float]]] = {}
        for a in accesses:
            covered.setdefault(a.bus_id, set()).add((a.start, a.end))
        for bus_id, spans in covered.items():
            bucket = result.frames.get(bus_id, {}).get(int(Level.SEMANTIC))
            if bucket:
                bucket[:] = [f for f in bucket if f.decoder_id == DECODER_ID or (f.start, f.end) not in spans]
    result.extend(a.to_frame() for a in accesses)
    result.sort()
    return accesses

