# PROGRESS

## Prompt 00 -- Python 3.15 lazy imports (fallback)

User chose the fallback on 2026-09-29: stay on the current interpreter, migrate
to 3.15 once DearPyGui ships a cp315 wheel.

Plan:
- [x] Audit heavy imports. Only module-level one is `pyvisa` in
      `better_scope/instruments/discovery.py`. numpy, PIL, win32clipboard,
      win32con and pymeasure are already function-local (deferred on every
      version), so they stay local -- moving them to module level would make
      them eager on 3.13.
- [x] Add `__lazy_modules__ = ["pyvisa"]` to `discovery.py` (before the import).
- [x] Mark 00 as fallback in `prompt_que/README.md` with the re-run note.
- [x] `uv run pytest` green.

## Interim move to Python 3.14 (2026-09-29)

- [x] `requires-python = ">=3.14,<3.15"` (cap until DearPyGui has cp315).
- [x] README Requirements updated.
- [x] `uv lock` + `uv sync` on CPython 3.14.4; all deps resolved with wheels.
- [x] `uv run pytest` green (17 passed); app launches and stays up.
- run_app.bat / run_app.ps1 are version-agnostic, no change needed.

## Prompt 01 -- Decode core, plugin API, UART (2026-09-29)

Design decisions (not dictated by the prompt):
- `BusConfig` / `Threshold` live in `decode/model.py` (per the README layout);
  `engine.py` imports them.
- Stacking: a bus names its *top* decoder; the engine resolves the chain
  root-first (e.g. pmbus -> smbus -> i2c) and runs every layer. Roles come from
  the root decoder, options are one flat dict (layers declaring the same option
  id share it). All layers' frames carry the bus's `bus_id`.
- `Decoder.require_one_of` class var declares "at least one of these roles"
  (UART rx/tx, SPI mosi/miso) so the engine and GUI can check it.
- `Frame.text` is a tuple of label variants, longest first.
- Registry collects classes that define `id` in their own class body; file
  plugins are shared across registries (loaded once per path+mtime).
- numpy is listed in `__lazy_modules__` (00 fallback); decode is not imported
  at app startup.

Plan:
- [x] `decode/model.py`: Level, LogicSignal, Frame, DecodeResult, Threshold, BusConfig
- [x] `decode/digitize.py`: Schmitt trigger, auto threshold, from-bool path
- [x] `decode/api.py`: Role, Option, Decoder, DecodeError
- [x] `decode/registry.py`: generic Registry + decoder registry
- [x] `decode/engine.py`: run()
- [x] `decode/decoders/uart.py` + estimate_baud
- [x] `decode/export.py`: frames_to_csv
- [x] `tests/signals.py` + UART / registry / core tests
- [x] `docs/DECODERS.md`, README link, mark 01 Done in prompt_que/README.md
- [x] `uv run pytest` green, commit

Notes for later prompts:
- UART marks a first character that framed cleanly without enough preceding
  idle as `data["uncertain"] = True` (capture may have started mid-stream).
- PMBus -> SMBus PEC size hints: resolved in prompt 02 (`hints_for_lower`).
- 11M-sample UART record decodes in ~2.6 s (digitize + frame building).

## Prompt 02 -- I2C, SMBus, PMBus, SPI decoders (2026-09-29)

Design decisions (not dictated by the prompt):
- PEC size hints: a generic downward hint hook instead of PMBus re-checking
  PEC. `Decoder.hints_for_lower(opts)` (classmethod, default `{}`) returns
  static hints; the engine merges the hints of every layer above a decoder
  (nearest wins) into `decoder.hints` before it runs. PMBus publishes
  `smbus.command_sizes` = {code: CommandSize(write, read)} from its command
  table; SMBus uses it to place the PEC byte and pick the transaction type.
  Static per-command sizes are all PMBus needs, and the hook stays reusable
  for private stacks (prompt 10).
- PMBus command table sourced from PMBus Part II rev 1.3.1 (2015-03-13,
  free on pmbus.org), Table 31. Rev 1.3.1 names C4h-FDh MFR_SPECIFIC_xx
  (the prompt said D0h-FDh); the spec wins.
- SMBus timeout check works from the I2C transaction's longest SCL-low
  interval (`max_scl_low` in the I2C level-2 frame data), 25 ms = tTIMEOUT,MIN.
- SPI no-CS idle gap is in clock periods (median sample-edge spacing), default 10.

Plan:
- [x] api.py / engine.py: hints hook + test
- [x] decoders/i2c.py
- [x] decoders/smbus.py (CRC-8, classification, PEC off/on/auto, timeout)
- [x] decoders/pmbus_commands.py + decoders/pmbus.py
- [x] decoders/spi.py
- [x] tests/signals.py: i2c_waveform, spi_waveform
- [x] tests: i2c, smbus, pmbus, spi
- [x] docs/DECODERS.md: stacking example + hints; mark 02 Done
- [x] uv run pytest green, commit

Notes for later prompts:
- Register mapper (03): PMBus default map = `pmbus_commands.COMMANDS`
  (code -> name, write/read SMBus protocol, data_bytes). PMBus semantic frames
  carry `command`, `name`, `direction`, `data`; SMBus frames carry `type`,
  `command`, `count`, `write_data`, `read_data`, `data`, `pec_ok`.
- A user map could also feed `smbus.command_sizes` hints (sizes for MFR
  codes) so PEC auto works for manufacturer commands; not done yet.
- Without hints, SMBus prefers fixed sizes over block shapes: a Block Write
  of count 1 looks like a Write Word, count 3 like a Write 32.
- PEC auto without hints cannot catch a bad PEC (it just looks like data).
- 4.8M-sample PMBus record (2000 commands) decodes in ~0.55 s.
