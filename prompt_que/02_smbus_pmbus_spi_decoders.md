# 02 -- I2C, SMBus, PMBus, and SPI decoders

Read `prompt_que/README.md` first. Requires prompt 01 (decode core + API).

## Goal

Add the remaining v1 built-in decoders on the prompt 01 API:
`i2c` -> `smbus` (stacked) -> `pmbus` (stacked), and `spi`. Extend the synthetic
signal helpers and `docs/DECODERS.md` (stacking example).

## I2C (`decoders/i2c.py`) -- base layer
- Roles: `scl`, `sda` (both required).
- Frames: START, repeated START, STOP, address (7-bit) + R/W, ACK/NACK, data
  byte, and a level-2 transaction from START to STOP. Tolerate clock stretching.
- Option: address display as 7-bit or 8-bit (with the R/W bit).
- 10-bit addressing: detect and flag as unsupported, don't mis-decode.

## SMBus (`decoders/smbus.py`) -- stacks on i2c
Source: SMBus spec (3.3.1 is freely downloadable at smbus.org/specs). Cite the
revision in the docstring.
- Classify each transaction: Quick Command, Send Byte, Receive Byte, Write
  Byte/Word, Read Byte/Word, Block Write/Read, Process Call, and
  Block Write-Block Read Process Call. Classification comes from the byte count
  and the repeated-START/direction pattern.
- PEC: CRC-8, poly 0x07, over every byte including address bytes. The option is
  off/on/auto. `auto` is ambiguous without knowing the command size, so it
  takes size hints from a stacked layer (PMBus) when available, otherwise it
  guesses and marks the result "PEC?". Flag PEC mismatch as an error frame.
- Output a level-2 frame per transaction: `data = {address, direction/type,
  command, payload bytes, pec, pec_ok}`. The register mapper (prompt 03) uses
  this.
- Flag NACKs clearly. SMBus timeout checks (25-35 ms clock low) are an option,
  off by default.

## PMBus (`decoders/pmbus.py`, `decoders/pmbus_commands.py`) -- stacks on smbus
- `pmbus_commands.py`: a code-to-{name, transaction type, data bytes} table for
  the standard command set. Source: PMBus Part II command summary (rev 1.2 is
  free in the pmbus.org specification archives; newer revisions are free with
  registration). Cite the revision. Don't guess entries. If the spec is not
  accessible, stop and ask the user for it, or cross-check against a public
  device datasheet and say which one.
- Codes 0xD0-0xFD are shown as `MFR_SPECIFIC_xx`. 0xFE/0xFF are flagged as
  extended commands (not decoded further in v1).
- Label each transaction with the command name, and feed the expected data size
  back to the SMBus PEC check.
- v1 shows payloads as raw hex only. Linear11/Linear16/VOUT_MODE/PAGE/VID
  decoding is deferred to prompt 09.
- Treat the PMBus command table as the device's default "register map", so
  prompt 03's mapper can label PMBus traffic even without a user Excel map. A
  user map overrides it by command code.

## SPI (`decoders/spi.py`)
- Roles: `sclk` (required), `mosi`, `miso` (at least one), `cs` (optional).
- Options: mode 0-3 (CPOL/CPHA), CS active low/high, bit order MSB/LSB, word
  size 4-32 (default 8), and "no CS" mode with an idle-gap transaction split.
- Frames: bits (level 0), words per line (level 1; MOSI and MISO paired in the
  same frame), and a transaction per CS assertion (level 2) with the full MOSI
  and MISO word lists. Flag an incomplete word at CS de-assert as an error.

## Tests
- Extend `tests/signals.py` with `i2c_waveform(transactions, freq, ...)` and
  `spi_waveform(words, mode, ...)`, both analog with noise and edge time.
- I2C: write, read with repeated START, NACK on address, and clock stretching.
- SMBus: one test per transaction type, PEC good and bad, and PEC auto with
  PMBus size hints.
- PMBus: known commands are named (e.g. 0x21 VOUT_COMMAND, 0x8B READ_VOUT),
  the MFR range is labeled, and an unknown command is shown raw.
- SPI: all four modes, MSB/LSB, 16-bit words, no-CS mode, and a truncated word.

## Out of scope
Register maps and field decoding (prompt 03), PMBus numeric formats (09), SVID (08).
