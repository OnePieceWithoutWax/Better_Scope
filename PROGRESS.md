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
- PMBus -> SMBus PEC size hints (prompt 02) need a feedback path; the engine
  currently only passes frames upward. Options: PMBus re-checks PEC itself, or
  add a hint hook to the stacked-decoder API.
- 11M-sample UART record decodes in ~2.6 s (digitize + frame building).
