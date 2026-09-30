# Register maps

A register map turns decoded bus traffic into register reads and writes:

```
WR VOUT_CFG = 0xCC : EN=1, MODE=Auto, VSEL=0x0C
RD VOUT = 0x9234 : RANGE=2-5 V, RESERVED[13:12]=0x1, CODE=0x234
```

Every access becomes one entry in a **command history** (time order) and one
level-3 annotation on the plot. Register *state* is not tracked: each access
is decoded on its own.

v1 reads Excel workbooks. More formats (YAML, SystemRDL, IP-XACT, SVD, CSV)
and PMBus numeric encodings are planned; they will plug in the same way (see
[Adding an importer](#adding-an-importer)).

## Contents

1. [The Excel template](#the-excel-template)
2. [Value formats](#value-formats)
3. [Header aliases](#header-aliases)
4. [Validation errors](#validation-errors)
5. [Binding a map to a bus](#binding-a-map-to-a-bus)
6. [SMBus and PMBus](#smbus-and-pmbus)
7. [SPI frame layout](#spi-frame-layout)
8. [Adding an importer](#adding-an-importer)

## The Excel template

Start from [`resources/register_map_template.xlsx`](../resources/register_map_template.xlsx)
(regenerate it with `uv run scripts/make_regmap_template.py`). Use one
workbook per device.

**Sheet `Device`** (optional): two columns, key and value.

| Key          | Meaning                                         | Default    |
|--------------|-------------------------------------------------|------------|
| `name`       | Device name                                     | file name  |
| `bus`        | `smbus`, `pmbus` or `spi`                       | `smbus`    |
| `address`    | 7-bit SMBus address                             | none       |
| `addr_width` | Register-address width in bits                  | 8          |
| `data_width` | Default register width in bits                  | 8          |

**Sheet `Registers`** (if no sheet has that name, the first sheet other than
`Device` is used): **one row per field**.

| Register | Address | Width | Access | Reset | Field | Bits | Description | Enum |
|----------|---------|-------|--------|-------|-------|------|-------------|------|
| VOUT_CFG | 0x1C    | 8     | RW     | 0x0C  | EN    | 7    | Output enable | 0=Off; 1=On |
|          |         |       |        |       | MODE  | 6:5  | Switching mode | 0=Off; 1=PWM; 2=Auto |
|          |         |       |        |       | VSEL  | 4:0  | Output voltage select | |
| DEVICE_ID | 0x30   | 8     | RO     | 0x5A  |       |      | Device identifier | |

- Fill the register-level columns (`Register`, `Address`, `Width`, `Access`,
  `Reset`) on the register's first row only. A following row with both
  `Register` and `Address` empty is another field of the same register.
  Repeating the same name and address on every row also works.
- A register with no field rows is shown as a raw value.
- `Width` defaults to the device's `data_width`.
- `Access` (`RW`, `RO`, `WO`, `W1C`, ...) is informational in v1.
- On a row without a field, `Description` describes the register; on a
  field row it describes the field.
- The header row does not have to be row 1: the first 20 rows are searched
  for one containing `Register` and `Address` headers.
- Other sheets (like the template's `Notes`) are ignored.

## Value formats

| Column             | Accepted                                              |
|--------------------|-------------------------------------------------------|
| Address, Reset, Width, Device `address` | `0x1A`, `1Ah`, `0b11010`, `26` (a number cell works too) |
| Bits               | `7:4`, `[7:4]`, `7..4`, `3`, `[3]` -- or separate `MSB` and `LSB` columns |
| Enum               | `0=Off; 1=On; 2=Auto` (`;` or one item per line; `:` instead of `=` is fine; commas only if there is no `;`) |

Excel turns a typed `7:4` into the time 07:04. The importer reads that back
as bits 7:4, but formatting the Bits column as Text (the template does) avoids
the surprise.

Bits that no field covers are shown as `RESERVED[msb:lsb]` when they are
non-zero.

## Header aliases

Headers are matched case-insensitively; `_`/`-` count as spaces and a
parenthesised suffix is ignored (`Width (bits)` = `Width`).

| Column      | Also accepted                                                        |
|-------------|----------------------------------------------------------------------|
| Register    | Reg, Register Name, Reg Name, Name                                   |
| Address     | Addr, Offset, Reg Addr, Register Address, Reg Address, Command, Command Code, Cmd, Code |
| Width       | Size, Reg Width, Register Width, Bit Width                           |
| Access      | Access Type, RW, R/W, Type                                           |
| Reset       | Reset Value, Default, Default Value, POR                             |
| Field       | Field Name, Bitfield, Bit Field                                      |
| Bits        | Bit, Bit Range, Range, Position                                      |
| MSB / LSB   | Bit MSB, High Bit / Bit LSB, Low Bit                                 |
| Description | Desc, Comment, Comments, Notes                                       |
| Enum        | Enums, Values, Enumeration, Encoding, Value Map                      |

Device-sheet keys: `name` (Device, Device Name, Part, Part Number), `bus`
(Bus Type, Interface, Protocol), `address` (Addr, Device Address, I2C Address,
SMBus Address, Slave Address), `addr_width` (Address Width, Register Address
Width, Reg Addr Width), `data_width` (Register Width, Reg Width, Data Bits).
Unknown keys are ignored.

If your workbook uses another header, add it to `COLUMN_ALIASES` in
`better_scope/decode/regmap/excel.py`.

## Validation errors

Loading stops with a `RegmapError` that lists **every** problem, each with
its sheet, row and column:

```
map.xlsx: 2 problem(s)
  - sheet 'Registers', row 3, column G (Bits): field B [5] overlaps field A [7:4]
  - sheet 'Registers', row 9, column B (Address): duplicate address 0x10 (also register STATUS)
```

Checked: unparsable values, overlapping fields, a field outside the register
width, duplicate field names, duplicate register addresses, an address wider
than `addr_width`, a reset value or enum value that does not fit, field rows
before the first register, and bad Device-sheet values (unknown bus, an
8-bit address where a 7-bit one is expected).

## Binding a map to a bus

```python
from pathlib import Path

from better_scope.decode import BusConfig, run
from better_scope.decode.regmap import DeviceBinding, annotate, load_register_map

device = load_register_map(Path("pmic.xlsx"))
bus = BusConfig("pm", "PMIC", "smbus", role_map={"scl": "CH1", "sda": "CH2"})
result = run([bus], waveforms)

history = annotate(result, [DeviceBinding("pm", device)])
for access in history:
    print(f"{access.start * 1e3:9.3f} ms  {access.labels[0]}  {'; '.join(access.errors)}")
```

- `annotate()` adds one level-3 `regmap` frame per access to the result (so
  they reach the plot, the event table and `frames_to_csv`) and returns the
  `RegisterAccess` list. It drops other level-3 frames with exactly the same
  span (the PMBus command names), since the register frame carries the same
  name plus the fields. `map_frames()` returns the accesses without touching
  the result.
- `DeviceBinding(bus_id, device, address=None, spi_layout=SpiLayout())`.
  Put several bindings on one SMBus bus for several devices; the binding's
  `address` overrides `device.address`, and a binding with neither matches
  every address.
- Each `RegisterAccess` has the time span, bus, device name and address,
  direction, register address, raw value, data bytes, the `Register` (or
  `None`), decoded `fields` and `errors`.

## SMBus and PMBus

- Device = 7-bit address, register = command code.
- Value = the data bytes, **little-endian** (SMBus 3.3.1 section 6.5.4):
  a Read Word returning `34 92` is `0x9234`. Block transfers use the data
  bytes after the byte count.
- Send Byte is a command without data (`WR CLEAR_FAULTS`). Receive Byte has
  no command code and shows as a raw read. A Process Call gives two
  accesses: the write, then the read.
- Errors from the SMBus layer are carried over: address NACK, data NACK,
  PEC mismatch, timeouts, incomplete transactions. An address outside the map
  gets `unknown register 0xNN` and shows as a raw value. A byte count that
  does not match the register width is flagged as a size mismatch.
- **PMBus fallback:** a bus decoded with the `pmbus` decoder and no binding
  is labelled from the PMBus 1.3.1 standard command table (names only, no
  fields). A device whose `bus` is `pmbus` uses its own registers first and
  the standard table for every other code, so a map only has to list the
  manufacturer-specific registers. `DeviceBinding(bus_id)` with no device
  forces the standard table on any bus. Pass `auto_pmbus=False` to skip
  unbound PMBus buses.

## SPI frame layout

SPI has no standard register protocol, so each binding describes its frame
with `SpiLayout`. Bit positions count from the frame's **LSB** (bit 0 is the
last bit on the wire when MSB first).

| Field               | Meaning                                              | Default |
|---------------------|------------------------------------------------------|---------|
| `frame_bits`        | Bits per register access                             | 16      |
| `rw_bit`            | Position of the R/W flag (`None` = no flag)          | 15      |
| `read_value`        | Flag value that means read                           | 1       |
| `addr_lsb`, `addr_bits` | Address position and width                       | 8, 7    |
| `data_lsb`, `data_bits` | Data position and width                          | 0, 8    |
| `read_line`         | Line with the read data: `"miso"` or `"mosi"`        | `"miso"` |
| `default_direction` | Direction when `rw_bit` is `None`                    | `"write"` |

The words of a chip-select transaction are joined (first word most
significant) and split into `frame_bits` chunks, so an 8-bit word decode
works with a 16-bit layout and a burst of several frames under one CS gives
several accesses. A transaction whose bit count is not a multiple of
`frame_bits` is reported as an error with its raw words. The address and R/W
flag always come from MOSI; write data comes from MOSI and read data from
`read_line`.

**Example 1 -- default `[R/W:1][ADDR:7][DATA:8]`, R = 1.** Write 0xCC to
VOUT_CFG (0x1C), then read it back:

```
MOSI  1C CC   ->  0 0011100 11001100  ->  WR VOUT_CFG = 0xCC
MOSI  9C 00   ->  1 0011100 ........
MISO  00 A0   ->           10100000  ->  RD VOUT_CFG = 0xA0
```

```python
DeviceBinding("spi0", device)  # default layout
```

**Example 2 -- 24-bit `[ADDR:7][W/R:1][DATA:16]`, 0 = read.** Write 0x9234
to VOUT (0x21):

```
frame = 0x21 << 17 | 1 << 16 | 0x9234 = 0x439234
MOSI  43 92 34  ->  0100001 1 1001001000110100  ->  WR VOUT = 0x9234
```

```python
layout = SpiLayout(frame_bits=24, rw_bit=16, read_value=0,
                   addr_lsb=17, addr_bits=7, data_lsb=0, data_bits=16)
DeviceBinding("spi0", device, spi_layout=layout)
```

## Adding an importer

Importers are plugins, discovered like decoders (built-in modules in
`better_scope/decode/regmap/`, the `better_scope.regmap_importers` entry-point
group, and `.py` files in the plugin folders; see
[DECODERS.md](DECODERS.md#where-to-put-the-file)):

```python
from pathlib import Path

from better_scope.decode.regmap.importers import RegmapImporter
from better_scope.decode.regmap.model import Device, RegmapError, RegmapIssue


class MyFormatImporter(RegmapImporter):
    id = "myformat"
    name = "My register format"
    extensions = (".myregs",)

    def load(self, path: Path) -> Device:
        ...  # build Device / Register / Field; raise RegmapError(str(path), issues) on bad input
```

`load_register_map(path)` picks the importer by file extension.
