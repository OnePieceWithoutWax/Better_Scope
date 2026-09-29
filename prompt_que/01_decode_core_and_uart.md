# 01 -- Decode core, plugin API, and UART decoder

Read `prompt_que/README.md` first.

## Goal

Build the GUI-agnostic decode backend (`better_scope/decode/`): turning waveforms
into logic, the decoder plugin API, plugin discovery, the engine that runs a bus
configuration, and the first decoder (UART). Also write `docs/DECODERS.md`.
No GUI in this prompt.

## Existing code to integrate with

- `BetterScope.acquire_waveforms(sources)` returns `{source: (t_array, v_array)}`
  of scaled numpy arrays (pymeasure fork `get_multiple_waveforms`).
- `AppConfig` persists JSON to `~/.better_scope/config.json`. Bus configs will
  be stored there later (prompt 04), so keep `BusConfig` JSON-serializable.

## Design

### 1. Signals (`model.py`, `digitize.py`)
- `LogicSignal`: uniform time base (`t0`, `dt`), plus the transition sample
  indices and the initial level. Helpers: `level_at(t)`, `next_edge(t,
  rising|falling|any)`, `edges_between(t0, t1)`. Decoders work on edges, not
  samples, so long records stay fast.
- `digitize(t, v, threshold, hysteresis)`: vectorized numpy Schmitt trigger.
  Threshold modes are `auto` (midpoint of the 5th/95th percentiles) and
  `manual` (volts). Hysteresis defaults to a small fraction of the swing.
- Warn in the result (not an exception) when samples per bit/clock is under
  ~4, because edges will be unreliable.
- Digital-probe data (prompt 07) arrives already boolean. `LogicSignal` must
  be constructible from a boolean array directly.

### 2. Frames (`model.py`)
- `Frame`: `start`, `end` (seconds), `bus_id`, `decoder_id`, `kind` (e.g.
  `"start"`, `"byte"`, `"parity_error"`), `level` (0=bit, 1=word/byte,
  2=packet/transaction, 3=semantic/register), `data: dict`, `text` (short and
  long label variants), and `error: str | None`.
- `DecodeResult`: frames grouped by bus and level, plus warnings.

### 3. Decoder API (`api.py`)
The protocol *definition* is declarative class metadata. The *logic* is the
decode method. Sketch:

```python
class Decoder(ABC):
    id: ClassVar[str]            # "uart"
    name: ClassVar[str]          # "UART / RS-232"
    version: ClassVar[str]
    roles: ClassVar[tuple[Role, ...]]      # Role("rx", "RX", required=False)
    options: ClassVar[tuple[Option, ...]]  # Option("baud", "Baud", int, 115200)
    stacks_on: ClassVar[str | None] = None # e.g. "smbus" stacks on "i2c"

    def decode(self, signals: dict[str, LogicSignal], opts: dict) -> Iterator[Frame]: ...
    # Stacked decoders implement decode_frames(frames, opts) instead.
```

`Option` supports int, float, bool, choice (with values), and str, with
default and help text. The GUI (prompt 04) builds its config form from this
metadata, so no decoder-specific UI code is needed.

### 4. Registry (`registry.py`)
Discovery order, where a later entry with the same `id` wins with a logged
warning:
1. Built-ins in `better_scope/decode/decoders/`.
2. Entry points, group `better_scope.decoders` (private pip packages).
3. `.py` files in `~/.better_scope/plugins/` and in each directory listed in the
   `BETTER_SCOPE_PLUGIN_PATH` env var (os.pathsep-separated), loaded with
   `importlib.util.spec_from_file_location`.

A plugin that fails to import or validate is logged and skipped, never fatal.
Validation checks: unique role ids, option defaults match their types, and
`stacks_on` refers to a known decoder. Use the same mechanism for register-map
importers later (group `better_scope.regmap_importers`), so make it generic.

### 5. Engine (`engine.py`)
- `BusConfig`: `bus_id`, `name`, `decoder_id`, `role_map` ({role: source
  name, e.g. "rx": "CH2"}), per-source threshold settings, and options. It
  round-trips through JSON.
- `run(bus_configs, waveforms) -> DecodeResult`: digitizes each needed source
  once (cached per threshold setting), runs base decoders, then stacked
  decoders in dependency order.

### 6. UART decoder (`decoders/uart.py`)
- Roles: `rx`, `tx` (each optional, at least one required).
- Options: baud, data bits 5-9, parity (none/even/odd/mark/space), stop bits
  (1/1.5/2), bit order (LSB default), invert (RS-232 levels; threshold ~0 V),
  idle-gap packet grouping (in bit times, 0 = off), and display (hex/ASCII/dec).
- Sample at bit centres timed from the start-bit falling edge. Re-sync on every
  start bit.
- Frames: start, data bits (level 0), byte (level 1) with value, parity error,
  framing error (bad stop bit), break (line held low > one frame time), and
  packet (level 2) when idle-gap grouping is on.
- Nice-to-have: an `estimate_baud(signal)` helper based on the shortest pulse,
  which snaps to standard rates.

### 7. CSV export (`export.py`)
`frames_to_csv(result, path, levels=...)` with columns time_start, time_end,
bus, decoder, level, kind, text, data (JSON), and error.

### 8. `docs/DECODERS.md`
A how-to for plugin authors. Cover: the class metadata fields, roles vs
options, the `LogicSignal` helpers, emitting frames and levels, stacking,
error frames, where to put the file (the three discovery locations), testing
with the synthetic-signal helpers, and an annotated walkthrough of
`uart.py` as the reference example. Link it from the root README.

## Tests
- Synthetic waveform helpers in `tests/signals.py`: `uart_waveform(bytes,
  baud, ...)` generating analog samples with configurable edge time, noise,
  offset, and oversampling ratio. Later prompts extend this with I2C and SPI
  helpers.
- UART round-trips across data bits/parity/stop bit settings and inverted
  RS-232 levels. Cover parity, framing, and break errors, a capture that starts
  mid-byte, and a capture that ends mid-byte.
- Registry: built-in discovery, a plugin loaded from a temp folder via
  `BETTER_SCOPE_PLUGIN_PATH`, a broken plugin being skipped, and an id override.
- `BusConfig` JSON round-trip.

## Out of scope
GUI, register maps, I2C/SMBus/SPI (prompt 02), file loading (prompt 05).
