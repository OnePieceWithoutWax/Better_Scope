# prompt_que -- Serial decoder feature

Queued implementation prompts for adding a software serial-bus decoder with
register-map annotation to Better_Scope. Run them in order, one session each.
Every prompt assumes you have read this file first.

## Order and dependencies

| #  | Prompt | Depends on | Status |
|----|--------|------------|--------|
| 00 | [00_python315_lazy_imports.md](00_python315_lazy_imports.md) | -- | Done on fallback (2026-09-29): on Python 3.14, `__lazy_modules__` only. Re-run the "Full migration" section once DearPyGui ships a cp315 wheel |
| 01 | [01_decode_core_and_uart.md](01_decode_core_and_uart.md) | 00 (or its fallback) | Done (2026-09-29) |
| 02 | [02_smbus_pmbus_spi_decoders.md](02_smbus_pmbus_spi_decoders.md) | 01 | Done (2026-09-29) |
| 03 | [03_register_map_excel.md](03_register_map_excel.md) | 02 | Done (2026-09-29) on the default template schema; refit aliases when an example map is supplied |
| 04 | [04_decode_gui.md](04_decode_gui.md) | 01-03 | Ready |
| 05 | [05_waveform_save_and_readback.md](05_waveform_save_and_readback.md) | 01, 04 | Ready |
| 06 | [06_live_decode.md](06_live_decode.md) | 04 | Ready (needs hardware to verify) |
| 07 | [07_digital_channels.md](07_digital_channels.md) | 01, 04 | Ready (needs a TLP058 probe to verify) |
| 08 | [08_svid_decoder.md](08_svid_decoder.md) | 02, 10 | Gated: Intel SVID spec is under NDA |
| 09 | [09_register_map_formats_and_encodings.md](09_register_map_formats_and_encodings.md) | 03 | Ready after 03 |
| 10 | [10_private_protocol_plugins.md](10_private_protocol_plugins.md) | 01 | Ready (SVI3 stays a stub until spec details are supplied) |

Mark a row Done here when its prompt is finished.

## Decisions already made (do not re-ask)

- **Software decode only.** Assume the scope has no serial-decode license and
  cannot report decode results. Decode runs on transferred or loaded waveforms.
  Proprietary protocols must be supported.
- **Inputs:** analog channels are required. Digital (FlexChannel/TLP058) is
  wanted (prompt 07). Saved waveforms can be loaded and decoded offline with no
  scope connected (prompt 05). Anything loaded is decoded the same way.
- **Mode:** single capture first. Live decode is an opt-in, triggered by a new
  acquisition or the scope entering the stopped state, not by a timer. It is
  expected to be slow (prompt 06).
- **Protocol definitions are Python files.** No spec-file DSL. The built-in
  decoders double as examples, and `docs/DECODERS.md` documents how to write one.
- **v1 protocols:** UART (RS-232 = UART with an invert option), I2C as the
  base layer, SMBus/PMBus on top of it, and SPI. SVID only if it can be
  done without protected material (prompt 08).
- **Register maps:** Excel only for v1, simplest version first. Other formats
  and encodings go in prompt 09. Registers have a fixed address and field
  definitions (SPI-like). Show "register X written/read with fields = values".
  Keep a command history. Do **not** track register state over the capture.
- **Outputs:** plot annotations (hideable per bus/level), an event table in a
  separate panel, and CSV export.
- **Dependencies:** the user approved all new dependencies for this feature
  (openpyxl, PyYAML, systemrdl-compiler, peakrdl-ipxact, cmsis-svd, etc.).
  Still list each one you add in the session summary.
- **Python 3.15+ with PEP 810 lazy imports** on all heavy imports -- see
  prompt 00 for the blocker and fallback.
- **Proprietary protocols** (AMD SVI3, anything under NDA) live outside this
  repo and load as plugins (prompt 10). Never commit their specs or code here.

## Shared conventions for every prompt

- Follow the user's global CLAUDE.md. Invoke the `python-rules` skill before
  touching any `.py` file. Write/update `PROGRESS.md` with the plan before
  editing. Stage files by name.
- Keep the decode backend GUI-agnostic (like `better_scope/core.py`): no
  `dearpygui` imports outside `better_scope/gui/`. It must be usable from a
  script or notebook.
- Slow work (VISA transfer, decoding long records) runs through
  `gui/worker.py`. UI mutations stay on the main thread.
- Heavy imports (numpy, openpyxl, yaml, pyvisa, pymeasure, PIL, win32*,
  systemrdl) are lazy. Use `lazy import` / `lazy from` on 3.15+. If prompt
  00 took its fallback, list them in a module-level `__lazy_modules__`
  instead. Either way, no heavy import runs at app startup unless the first
  frame needs it.
- Tests run without hardware. Decoders are tested against **synthetic
  waveforms** generated in `tests/` (analog with noise and edge time, not just
  ideal logic), including error cases (NACK, parity error, bad PEC, framing
  error, truncated capture).
- MIT license: do not copy sigrok/libsigrokdecode code (GPL). Reading it for
  protocol understanding is fine; write every implementation fresh.
- No protected specs in the repo. Cite public sources (spec revision, datasheet)
  in module docstrings where a table or constant comes from.

## Target package layout (created by 01-03, extended later)

```
better_scope/decode/
    __init__.py
    model.py        # LogicSignal, Frame, Annotation, BusConfig, DecodeResult
    digitize.py     # waveform -> logic (threshold/hysteresis) -> edges
    api.py          # Decoder base class, Role, Option declarations
    registry.py     # discovery: built-ins, entry points, plugin folders
    engine.py       # run a BusConfig over captured signals, stack decoders
    export.py       # CSV export of frames / register accesses
    decoders/
        uart.py  i2c.py  smbus.py  pmbus.py  pmbus_commands.py  spi.py
    regmap/
        model.py    # Device, Register, Field
        excel.py    # Excel importer
        mapper.py   # frames -> RegisterAccess (command history)
docs/
    DECODERS.md     # how to write a decoder plugin
    REGISTER_MAPS.md
```
