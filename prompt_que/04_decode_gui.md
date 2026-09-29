# 04 -- Decode GUI: bus configuration, plot annotations, event table, export

Read `prompt_que/README.md` first. Requires prompts 01-03.

## Goal

Expose the decoder in the app: configure buses, run decode on acquired
waveforms, show annotations on the plot (hideable), show an event table in a
separate panel, and export CSV.

## Existing GUI to fit into
- `gui/app.py` builds tabs. Each tab class has `build(parent)`,
  `on_connection_changed(connected)`, and optionally `tick()`, called per frame
  from the render loop.
- `gui/plot_tab.py` acquires `CHn` sources through the worker and draws
  `add_line_series`. It converts numpy arrays with per-element `float()`
  loops, which is slow for long records. Replace this with `.tolist()` and
  min/max decimation to around 2x the plot's pixel width (keep full-resolution
  data for decoding).
- `gui/worker.py`: `submit(fn, on_done, on_error)`. All widget changes happen on
  the main thread.

## Decode tab (new, `gui/decode_tab.py`)
- Bus list: add, remove, duplicate, enable/disable, and rename.
- Per bus: pick a decoder from the registry, a role-to-source dropdown per
  `Role` (the CH1..CHn list from the connected model, plus loaded/offline
  sources later), a threshold per source (auto/manual + hysteresis), and an
  options form **generated from the decoder's `Option` metadata**. No
  decoder-specific UI.
- Register maps: load an Excel map and bind it to a bus + device address (SMBus)
  or a bus + SPI layout. Show parse errors inline.
- Persist bus configs and map bindings in `AppConfig` (new field, e.g.
  `decode_buses: list`). Store map file paths, not map contents.
- A "Plugins" section lists discovered decoders with their source (built-in,
  entry point, or folder path), plus plugins that failed to load and why.

## Running decode
- After a Plot-tab acquisition finishes, run `engine.run` for enabled buses on
  the worker thread. Channels needed by enabled buses are included in the
  acquisition automatically.
- Also add a "Decode" button that acquires and decodes in one step.
- Show warnings (undersampling, missing channel) in the status line.

## Plot annotations (Plot tab)
- Use `dpg.subplots` with linked X axes: waveforms on top, one decode lane per
  bus below. Draw frames as boxes with centred text in plot coordinates, and
  errors in a distinct colour.
- Show/hide toggles per bus and per level (bits / bytes-words / transactions /
  register). Default: bytes + register on, bits off.
- Performance: draw only frames inside the visible X range, and cap the item
  count (skip text when boxes are too narrow). Redraw on axis-range change,
  checked in `tick()`, not every frame.

## Event table (separate panel)
- A non-modal, resizable DearPyGui window opened from the Decode tab or Plot
  tab ("Event Table").
- Rows are the command history: time, bus, device, R/W, register/command,
  value (hex), fields (`NAME=value; ...`), and error.
- Filters: bus, device, register text, and errors-only. Clicking a row centres
  the plot X axis on that frame.
- Use a virtualized or paged `dpg.table` if there are more than a few thousand
  rows.

## CSV export
Buttons: "Export events CSV" (register accesses / command history) and "Export
frames CSV" (all frames at selected levels), both using `decode/export.py`.
Default the save location to the capture save directory.

## Help
Update `resources/Better_Scope_Help.md` with a Decode section, linking
`docs/DECODERS.md` and `docs/REGISTER_MAPS.md`.

## Verification
- Unit tests for any non-GUI helpers added (decimation, visible-range filtering).
- Run the app. With no scope connected, use a temporary debug path or prompt
  05's loader (whichever exists) to feed synthetic waveforms, and confirm the
  lanes, toggles, table, and export work. Don't leave debug code committed.

## Out of scope
Loading files (prompt 05), live decode (prompt 06), digital channels (prompt 07).
