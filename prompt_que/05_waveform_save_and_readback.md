# 05 -- Save waveforms and read them back for offline decode

Read `prompt_que/README.md` first. Requires prompts 01 and 04.

## Goal

Save acquired waveforms and load saved ones back, from this app and from
the scope. Anything loaded is plotted and decoded exactly like a live
acquisition, with no scope connected.

## Existing hooks
- `AppConfig.save_waveform: bool` exists but nothing uses it. Wire it in (save
  waveform data alongside a capture) or remove it if it doesn't fit, and say
  which you chose.
- The pymeasure fork has `waveforms.save_waveform_csv(filename, source)` and
  `Save` subsystem commands. These save on the **scope's** filesystem, not the
  PC.

## Formats
1. **Native** (`.npz`, via numpy): per-source time base (`t0`, `dt`) plus
   samples as float32, a `meta` JSON string (scope id, capture time, units,
   source names, app version), and optionally the bus configs and register-map
   bindings in use. The file should reproduce the decode on reload.
2. **Tektronix CSV** as saved by the 4/5/6 Series scope: a variable-length
   header block, then a `TIME,CH1,...` column header row. Parse the header
   into meta, detect the column row case-insensitively, and support
   multi-channel files. Ask the user for a sample file if none is in
   `resources/examples/`, and don't guess the header layout from memory alone.
3. **Tektronix `.wfm`**: implement only if the user supplies a sample file.
   Write a small reader based on Tek's published reference waveform file-format
   document (verify the version string in the file, e.g. `WFM#003`).
   **Don't use `tm_data_types`**: it is capped at Python <3.14 and pulls
   numba/scipy.
4. Generic CSV: a first column of time, then one column per source.

## Backend
- `better_scope/waveform_io.py` (or `decode/io.py`): `save_waveforms(path,
  waveforms, meta)` and `load_waveforms(path) -> (waveforms, meta)`, with the
  format chosen by extension or sniffing. The return shape matches
  `acquire_waveforms`.
- Long records: load with numpy, not row-by-row Python.

## GUI
- Plot tab: "Save waveforms..." and "Load waveforms...". Loaded sources appear
  as selectable sources (e.g. `FILE:CH1`) and can be used in bus role mapping
  while disconnected.
- A loaded native file with embedded bus configs offers to apply them.

## Tests
Native round-trip (including embedded bus configs), a Tek CSV fixture (built
from the user's sample, or a documented synthetic one), generic CSV, and
decoding a loaded file matches decoding the original arrays.
