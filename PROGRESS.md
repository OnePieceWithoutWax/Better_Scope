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

## Prompt 03 -- Register maps (Excel) and register-access mapper (2026-09-29)

No example map in `resources/examples/`: using the default template schema.

Design decisions (not dictated by the prompt):
- Importer plugins subclass `regmap.importers.RegmapImporter` (`id`, `name`,
  `extensions`, `load(path) -> Device`), discovered by the generic `Registry`
  from `better_scope.decode.regmap` + entry-point group
  `better_scope.regmap_importers` + plugin folders. `load_register_map(path)`
  picks the importer by file extension.
- Validation collects every problem (`RegmapIssue`: sheet/row/column/message)
  and raises one `RegmapError` listing them all.
- Excel stores a typed `7:4` as a time (07:04); the Bits parser accepts it.
- Bindings: `DeviceBinding(bus_id, device, address, spi_layout)`. `device=None`
  = PMBus standard table only. A device with bus `pmbus` falls back to the
  PMBus table for codes it does not define. `map_frames(..., auto_pmbus=True)`
  adds a PMBus-table binding for PMBus-decoded buses with no binding.
- SPI: a CS transaction is split into `frame_bits` chunks (so 8-bit words
  work with a 16-bit layout); bit positions count from the frame LSB.
- SMBus process calls produce two accesses (write then read).
- `annotate(result, bindings)` adds level-3 `regmap` frames and, by default,
  drops the PMBus level-3 frame with the same span (the register frame
  carries the same name plus the fields).

Plan:
- [x] `uv add openpyxl`
- [x] `regmap/model.py`: Device, Register, Field, FieldValue, decode_value, RegmapIssue/RegmapError
- [x] `regmap/importers.py`: RegmapImporter base, registry, load_register_map
- [x] `regmap/excel.py`: parsers, importer, write_template
- [x] `resources/register_map_template.xlsx` + `scripts/make_regmap_template.py`
- [x] `regmap/mapper.py`: SpiLayout, DeviceBinding, RegisterAccess, map_frames, access_frames, annotate
- [x] tests: excel importer, field decode, mapper (SMBus, PMBus fallback, SPI default/custom)
- [x] `docs/REGISTER_MAPS.md`, README link, mark 03 Done
- [x] `uv run pytest` green, commit

Notes for later prompts:
- New dependency: openpyxl 3.1.5 (+ et-xmlfile).
- GUI (04): `annotate(result, bindings)` returns the command history and adds
  `regmap` level-3 frames; `RegisterAccess.labels` are the text variants.
  `write_template()` can back a "save template" button.
- 09: add importers as `RegmapImporter` subclasses; parsers `parse_int`,
  `parse_bits`, `parse_enum` in `regmap/excel.py` are reusable. A user map
  could also feed `smbus.command_sizes` hints (not done).
- The SMBus mapper trusts the SMBus classification: without size hints a
  2-byte payload on a byte register is a Write Word and is flagged as a
  size mismatch.

## Prompts 04 + 05 -- Decode GUI, waveform save/load (2026-09-29)

No Tek CSV or .wfm sample in `resources/examples/`: Tek CSV parser is
layout-agnostic (key/value header block, then a `TIME,...` row found
case-insensitively) and tested on a documented synthetic fixture; `.wfm` is
not implemented. Ask the user for real sample files.

Design decisions (not dictated by the prompts):
- GUI-agnostic helpers: `better_scope/plotting.py` (min/max decimation,
  visible-range frame filtering, label fitting), `decode/session.py`
  (`MapBinding`, `DecodeSession`: persisted buses + map bindings -> run +
  annotate; source remapping), `decode/events.py` (command-history rows,
  filters) and `export.events_to_csv`.
- `AppConfig.decode_buses` / `decode_maps` (lists of dicts, paths not maps).
- `save_waveform` is kept and wired: a screenshot capture with it on also
  saves the enabled analog channels as `<name>.npz` (with the bus configs
  and map bindings) next to the image. Config tab gets its checkbox.
- Loaded sources are named `FILE:<source>`; applying a file's embedded bus
  configs remaps their sources to the `FILE:` names and replaces the bus list.
- Plot: `dpg.subplots` (waveforms + one lane per shown bus, linked X). Lane
  rows are (level, decoder) pairs; boxes are draw items in plot space, text
  uses text points; redraw + re-decimation happen in `tick()` when the X
  range or plot width changes.

Plan:
- [x] `plotting.py` + tests
- [x] `decode/events.py`, `export.events_to_csv` + tests
- [x] `decode/session.py` + config fields + tests
- [x] `waveform_io.py` (npz, Tek CSV, generic CSV) + tests
- [x] core: save waveforms with capture; Config tab checkbox
- [x] `gui/decode_tab.py` (buses, roles, thresholds, options form, maps, plugins)
- [x] `gui/event_table.py` (non-modal, filters, paging, click-to-centre)
- [x] `gui/plot_tab.py` rewrite (subplots, lanes, decimation, save/load, decode hook)
- [x] `gui/app.py` wiring; Help Decode section
- [x] Run the app on synthetic data (scratch script, frame-buffer screenshots)
- [x] `uv run pytest` green, mark 04/05 Done, commit

Notes for later prompts:
- DearPyGui 2.3.1: draw items (`draw_rectangle`) parented to a plot render at
  the wrong place, ignore fill alpha and cover series. Lanes therefore use
  one `add_shade_series` per (row, colour) plus `add_text_point` labels.
- Worker bug fixed: `on_error` lambdas closed over the `except` variable,
  which Python unbinds after the block, so every error callback raised
  NameError. Errors now reach the UI.
- `PlotTab.load_result(path, waveforms, meta)` is the entry point for any
  loaded source (07 digital channels can reuse it); `DecodeTab.decode(waves)`
  queues latest-wins while a decode runs (06 live decode can call it).
- Decode runs after every Plot acquisition, including auto-refresh; a decode
  requested while one is running replaces the pending one.
- Events CSV exports the rows passing the Event Table filters.
- No real Tektronix CSV / .wfm sample yet: ask the user for both.

## Prompt 06 -- Live decode (2026-09-29)

The pymeasure fork's `Acquisition` subsystem already has `state`
(`ACQuire:STATE?`, control), `num_acquisitions` (`ACQuire:NUMACq?`) and
`stop_after`; the backend uses them.

Design decisions (not dictated by the prompt):
- `better_scope/live.py`: GUI-agnostic `LiveMonitor` state machine
  (`poll(running, num_acq) -> LiveAction`, `cycle_done(...)`) plus
  `live_cycle(scope, sources, stop_first)` (STOP, read NUMACq, transfer,
  RUN). The GUI drives it from `gui/live_panel.py`.
- New acquisition = NUMACq changed, or the scope was seen running since the
  last decode and is now stopped (Single pressed twice gives NUMACq 1 both
  times), or an app-armed single finished.
- Stopped + busy: wait, don't skip (the stopped data is still there). Running
  + decode-while-running + busy: skip and count (NUMACq delta, a counter
  reset by RUN counts from 0).
- Decode-while-running RUNs again right after the transfer, not after the
  decode. "Stop for transfer" is a checkbox (default on) so hardware can show
  whether the STOP is needed.
- Arm single: remembers `STOPAfter`, sets SEQuence + RUN; re-arms after each
  decode while live mode is on; restores `STOPAfter` when disarmed.
- Plot auto-refresh stays for plain plotting and no longer decodes; it pauses
  while live mode is on.
- `DecodeTab.decode(..., on_done)` reports decode duration (or `None` on
  failure/nothing to decode) so live mode knows when a cycle ends.

Plan:
- [x] core: `acquisition_status`, `run_acquisition`, `stop_acquisition`,
      `arm_single`, `set_stop_after`
- [x] `live.py`: LiveMonitor, LiveAction, live_cycle
- [x] decode_tab: `decode(on_done=...)`
- [x] `gui/live_panel.py` + Plot tab row; auto-refresh no longer decodes
- [x] tests: scripted fake STATE/NUMACq sequences
- [x] Help text, mark 06 Done (hardware verify pending), commit

Notes for later prompts:
- Skipped count assumes RUN resets NUMACq; if it does not, the delta after
  a STOP/RUN cycle is measured from NUMACq at the STOP (handled either way,
  but can undercount when many acquisitions arrive in one poll).
- Known gap: a front-panel Single that starts and finishes between two polls
  (0.3 s) with the same NUMACq as the last decode is not seen. Arm single
  from the app is reliable.
- Changing scope settings while stopped resets NUMACq, which reads as a new
  acquisition and re-decodes the same data (harmless).

## Prompt 07 -- Digital channels (FlexChannel / TLP058) (2026-09-29)

No programmer manual in the repo; query names are from the MSO 4/5/6
programmer manual as remembered and must be confirmed on hardware:
`CH<x>:PROBETYPE?` (fork: `channel.probe_type`, DIGITAL/ANALOG),
`DATa:SOUrce CH<x>_DALL`, `CURVe?` definite-length block, bit n = D<n>.

Design decisions (not dictated by the prompt):
- `better_scope/digital.py`: source names (`CH1_D0`), IEEE 488.2 block
  parsing, raw curve -> unsigned ints -> 8 boolean arrays.
- `BetterScope.digital_channels` detected on connect; `acquire_waveforms`
  splits `CHx_Dn` requests into one DALL transfer per channel (raw
  write/read_bytes, encoding saved and restored) and returns `(t, bool)`.
- Digital sources are `(t, bool_array)` tuples: the engine's `digitize`
  already takes bool arrays as-is, so no engine change.
- Plot: bool sources go in their own subplot as stacked 0/1 traces.
- Decode tab: threshold widgets replaced by "digital (scope threshold)".
- `.npz`: bool sources stored with `np.packbits` (`s{i}_packed` + count);
  CSV columns named `CHx_Dn` holding only 0/1 load as bool.
- Threshold read/write (optional in the prompt) not exposed.

Plan:
- [ ] `digital.py` + core detection/transfer
- [ ] waveform_io packbits
- [ ] plot tab digital subplot; decode tab threshold hiding
- [ ] tests: fake DALL payload split, UART/SPI decode from bool sources, npz round trip
- [ ] mark 07 Done (hardware verify pending), follow-up entries, commit
