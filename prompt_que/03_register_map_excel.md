# 03 -- Register maps (Excel) and the register-access mapper

Read `prompt_que/README.md` first. Requires prompts 01-02.

## Goal

Load a device register map from Excel, map decoded transactions to register
reads/writes, and label each access with register and field names/values.
Produce a **command history** (every access, in time order). **No register
state tracking.** Simplest working version first. Everything else goes to
prompt 09.

## First step: look for a real example
If the user has put an example map in `resources/examples/` (or given a path),
read it before designing the column mapping and fit the mapping to it. If
none exists, use the default template below and say so in the summary.

## Model (`regmap/model.py`)
- `Device`: name, bus type (smbus/pmbus/spi), default address (for SMBus),
  register-address width, data width, and registers by address.
- `Register`: name, address, width (bits), access (RW/RO/WO/W1C...,
  informational only in v1), reset value, description, fields.
- `Field`: name, lsb, width, description, and an optional `enum: dict[int, str]`.
- `decode_value(register, raw) -> list[FieldValue]` (name, raw field value,
  enum label). Bits not covered by any field show as `RESERVED[msb:lsb]` if non-zero.

## Excel importer (`regmap/excel.py`, openpyxl)
Default template, one workbook per device:
- Optional sheet `Device`: two columns (key, value) holding name, bus,
  address, addr_width, and data_width.
- Sheet `Registers` (or the first sheet): **one row per field**. Register-level
  columns only need to be filled on the register's first row and are
  forward-filled.
  Columns: `Register`, `Address`, `Width`, `Access`, `Reset`, `Field`,
  `Bits`, `Description`, `Enum`.
- Address/Reset formats: `0x1A`, `1Ah`, `26`, and `0b...`.
- Bits formats: `7:4`, `[7:4]`, `7..4`, `3`, or separate `MSB`/`LSB` columns.
- Enum format: `0=Off; 1=On; 2=Auto`.
- Header matching is case-insensitive with an alias table (e.g. `Addr`,
  `Offset`, `Reg Addr` -> Address). This lets the user's real workbook fit
  with small alias additions.
- Validation errors report sheet/row/column, not just a stack trace:
  overlapping fields, a field outside the register width, duplicate addresses,
  or an unparsable value.
- Register the importer through the generic registry (prompt 01, entry-point
  group `better_scope.regmap_importers`) so prompt 09 can add formats the same way.
- Commit a generated template at `resources/register_map_template.xlsx` plus
  the small script or test fixture that builds it.

## Mapper (`regmap/mapper.py`)
Takes level-2 transaction frames and bus-to-device bindings and produces
`RegisterAccess` records: time, bus_id, device address, READ/WRITE, register
address, raw value, `Register | None`, field values, and errors (NACK, PEC fail,
unknown register).
- **SMBus/PMBus:** device = 7-bit address, register = command code, and value =
  data bytes (little-endian per SMBus). With no user map, fall back to the PMBus
  command table from prompt 02 (name only).
- **SPI:** the user's registers use a fixed address plus field definitions, so
  the transaction layout is configurable per bus binding:
  `frame_bits`, `rw_bit` position and polarity, `addr_bits` (position and
  width), `data_bits` (position and width), and the data line for reads
  (MOSI/MISO). Default: 16-bit frame = `[R/W:1][ADDR:7][DATA:8]`, R=1.
  Document this in `docs/REGISTER_MAPS.md` with two worked examples.
- Emit level-3 `Frame`s ("WR VOUT_CFG = 0x1C : EN=1, MODE=Auto, VSEL=0x0C") so
  they flow into the plot and event table with the other frames.

## Docs
`docs/REGISTER_MAPS.md`: the template columns, formats, the alias table, SPI
layout options, how to bind a map to a bus/device, and a note that more formats
are coming (prompt 09).

## Tests
- Importer: template round-trip, alias headers, forward-fill, every
  Bits/Address format, and each validation error with its row number.
- Field decode: enums, reserved non-zero bits, and a 16-bit register.
- Mapper: SMBus write-byte/read-word to named fields, a PMBus fallback name, and
  SPI with the default and a custom layout. An unknown register shows raw.

## Out of scope
YAML/SystemRDL/IP-XACT/SVD/CSV, PMBus numeric formats, paging, multi-byte
endianness options, and state tracking (all prompt 09 except state tracking,
which is not wanted).
