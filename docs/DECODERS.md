# Writing a decoder

Better_Scope decodes serial buses in software, from waveforms transferred off
the scope or loaded from disk. Every protocol, built-in or private, is a
Python class. You don't need a spec-file DSL and you don't write any GUI code:
the configuration form is built from your class's metadata.

The decode backend (`better_scope/decode/`) has no GUI dependency, so the same
decoder runs in the app, in a script, or in a notebook.

## Contents

1. [Quick start](#quick-start)
2. [Class metadata](#class-metadata)
3. [Roles vs options](#roles-vs-options)
4. [Working with `LogicSignal`](#working-with-logicsignal)
5. [Emitting frames](#emitting-frames)
6. [Errors and warnings](#errors-and-warnings)
7. [Stacking decoders](#stacking-decoders)
8. [Where to put the file](#where-to-put-the-file)
9. [Testing with synthetic signals](#testing-with-synthetic-signals)
10. [Walkthrough: `uart.py`](#walkthrough-uartpy)
11. [Running a decode from a script](#running-a-decode-from-a-script)

## Quick start

```python
from better_scope.decode.api import Decoder, Option, Role
from better_scope.decode.model import Level


class PulseWidth(Decoder):
    id = "pulse_width"
    name = "Pulse width"
    version = "1.0"
    roles = (Role("in", "Input"),)
    options = (Option("min_us", "Min width (us)", float, 1.0),)

    def decode(self, signals, opts):
        sig = signals["in"]
        t = sig.t_start
        while (rise := sig.next_edge(t, "rising")) is not None:
            fall = sig.next_edge(rise, "falling")
            if fall is None:
                break
            width_us = (fall - rise) * 1e6
            if width_us >= opts["min_us"]:
                yield self.frame(rise, fall, "pulse", Level.WORD,
                                 data={"width_us": width_us},
                                 text=(f"{width_us:.2f} us", f"{width_us:.1f}"))
            t = fall
```

Save that as `~/.better_scope/plugins/pulse_width.py` and it shows up in the
decoder list the next time the registry is scanned.

## Class metadata

The metadata defines the protocol, and the decode method holds the logic.

| Attribute | Type | Meaning |
|-----------|------|---------|
| `id` | `str` | Unique key, stored in bus configs. **Required.** Re-using a built-in id overrides it. |
| `name` | `str` | Display name. **Required.** |
| `version` | `str` | Your decoder's version (shown in the GUI). |
| `description` | `str` | One-line summary. |
| `roles` | `tuple[Role, ...]` | Input signals (base decoders only). |
| `require_one_of` | `tuple[str, ...]` | Role ids of which at least one must be mapped (UART `rx`/`tx`, SPI `mosi`/`miso`). |
| `options` | `tuple[Option, ...]` | User settings. |
| `stacks_on` | `str \| None` | Id of the decoder whose frames this one consumes. `None` means it is a base decoder. |

The registry only collects classes that define `id` **in their own class
body**. That lets you write helper base classes or import other decoders into
your module without registering them twice.

The registry validates each class on load and skips it, with a logged reason,
when:

- `id` or `name` is missing or empty;
- role ids or option ids are duplicated;
- `require_one_of` names a role that doesn't exist;
- an option default or choice doesn't match its type, or the default isn't
  one of the choices;
- a base decoder doesn't implement `decode()` or has no roles, or a stacked
  decoder doesn't implement `decode_frames()`;
- `stacks_on` points at an unknown decoder, or the stack forms a cycle.

## Roles vs options

**Roles** are *signals*: which wire is SCL, which is RX. The user maps each role
to a source (`CH1`, later `D3` for digital probes) in the bus config. A role is
`Role(id, label, required=True, help="")`. Optional roles use
`required=False`, and `require_one_of` expresses "at least one of these".

**Options** are *settings*: baud rate, parity, bit order. An option is
`Option(id, label, type, default, choices=(), help="")`:

| Kind | Declare as | GUI widget |
|------|------------|------------|
| int | `Option("baud", "Baud", int, 115200)` | integer input |
| float | `Option("gap", "Gap (bits)", float, 0.0)` | float input |
| bool | `Option("invert", "Invert", bool, False)` | checkbox |
| str | `Option("label", "Label", str, "")` | text input |
| choice | `Option("parity", "Parity", str, "none", choices=("none", "even", "odd"))` | dropdown |

Choice values keep their declared type, so `choices=(1.0, 1.5, 2.0)` with
`float` works. The engine fills in defaults and coerces the user's values
(`"1.5"` becomes `1.5`, `"on"` becomes `True`). An invalid value produces a
warning and falls back to the default, so `decode()` always receives a
complete, typed `opts` dict.

## Working with `LogicSignal`

Before your decoder runs, the engine digitizes each mapped source with a
Schmitt trigger (auto or manual threshold, plus hysteresis, set per source in
the bus config). Digital-probe data skips that step. Either way, your decoder
receives a `LogicSignal`, which stores only the transitions. Walk edges rather
than samples, and multi-million-sample records stay fast.

| Member | Returns |
|--------|---------|
| `t_start`, `t_end` | First and last sample times (s) |
| `dt` | Sample interval (s) |
| `level_at(t)` | `bool` level at time `t` |
| `levels_at(times)` | Vectorized `level_at` for a numpy array |
| `next_edge(t, "rising" \| "falling" \| "any")` | Time of the first matching edge strictly after `t`, or `None` |
| `edges_between(t0, t1, kind="any")` | Numpy array of edge times with `t0 <= t < t1` |
| `edge_times` | All edge times |
| `samples_per(period)` | Samples in `period` seconds; use it for oversampling checks |
| `inverted()` | The same signal with levels flipped |
| `LogicSignal.from_bool(levels, dt, t0)` | Build one from a boolean array (useful in tests) |

`better_scope.decode.digitize.oversampling_warning(signal, rate_hz)` returns a
ready-made warning when there are fewer than about 4 samples per bit or clock.

## Emitting frames

`decode()` is a generator. Yield `Frame`s built with `self.frame(...)`, which
fills in `bus_id` and `decoder_id` for you:

```python
yield self.frame(start, end, kind, level, data={...}, text=("long", "short"), error=None)
```

- **`kind`** is a short tag: `"start"`, `"byte"`, `"ack"`, `"parity_error"`.
  The event table filters on it.
- **`level`** places the frame in an annotation row, and the user can hide
  rows per bus and per level:

  | `Level` | Value | Use for |
  |---------|-------|---------|
  | `BIT` | 0 | Individual bits, start/stop conditions |
  | `WORD` | 1 | Bytes and words |
  | `PACKET` | 2 | Transactions and packets |
  | `SEMANTIC` | 3 | Meaning: register accesses, named commands |

- **`data`** is the structured payload. Keep it JSON-serializable, because it is
  written to CSV as JSON and consumed by stacked decoders and the register
  mapper.
- **`text`** holds label variants, **longest first**. The plot picks the longest
  one that fits the box.

Frames can be yielded in any order. The engine sorts them by start time.

## Errors and warnings

There are three kinds of problem, each with its own mechanism:

- **A protocol error in the data** (parity, NACK, bad CRC, framing, truncated
  capture): emit a frame with `error="..."`. It stays on the plot, highlighted,
  and appears in the error filter. Never raise for these.
- **Something the user should know** (poor oversampling, suspicious timing):
  call `self.warn("...")`. Warnings are collected on the `DecodeResult`.
- **A configuration you cannot decode at all** (baud rate 0): raise
  `DecodeError("...")`. The engine turns it into a warning for that bus, and
  the other buses still decode.

Any other exception is also caught and logged as a warning, but treat that
as a bug in your decoder.

## Stacking decoders

A stacked decoder consumes the frames of the layer below instead of signals.
Set `stacks_on` and implement `decode_frames()`:

```python
class MyProtocol(Decoder):
    id = "myproto"
    name = "My protocol over I2C"
    stacks_on = "i2c"
    options = (Option("device_addr", "Device address", int, 0x40),)

    def decode_frames(self, frames, opts):
        for f in frames:
            if f.level == Level.PACKET and f.data.get("address") == opts["device_addr"]:
                yield self.frame(f.start, f.end, "command", Level.SEMANTIC, text=("...",))
```

A bus config names the **top** decoder of the stack (for example `"pmbus"`).
The engine then resolves the whole chain, `i2c -> smbus -> pmbus`, and runs
every layer bottom-up:

- Roles come from the root (base) decoder.
- Options from every layer go into one flat dict. Layers that declare the
  same option id share its value.
- `decode_frames()` receives **all** frames of the layer directly below,
  sorted by start time.
- Frames from every layer carry the bus's `bus_id` and their own
  `decoder_id`.

### The built-in stack: `i2c -> smbus -> pmbus`

Each layer consumes one frame kind from the layer below and adds meaning:

| Layer | Consumes | Emits (main frame) |
|-------|----------|--------------------|
| `i2c` | SCL/SDA signals | `transaction` at `Level.PACKET`, `data["segments"]` = one dict per START/Sr segment (`address`, `read`, `address_ack`, `data`, `acks`, `spans`) plus `complete` and `max_scl_low` |
| `smbus` | i2c `transaction` frames | `transaction` at `Level.PACKET`: `type` (`write_word`, `block_read`, ...), `command`, `data`, `pec`, `pec_ok` |
| `pmbus` | smbus `transaction` frames | `command` at `Level.SEMANTIC`: `name` (`VOUT_COMMAND`, ...), `direction`, `data` |

A private protocol on SMBus (say a vendor's register protocol) would set
`stacks_on = "smbus"` and read `f.data["command"]` and `f.data["data"]` from
the smbus `transaction` frames, exactly as `pmbus.py` does.

### Passing hints down

Frames only flow upward, but sometimes a lower layer needs knowledge that
only an upper layer has. SMBus can't tell a trailing PEC byte from one more
data byte unless it knows how many data bytes the command carries, and only
PMBus knows that. For this, a decoder can override the classmethod
`hints_for_lower(opts)` and return a dict of **static** hints. Before each
layer runs, the engine merges the hints of every layer above it into
`self.hints` (the nearest layer wins on a key clash):

```python
from better_scope.decode.decoders.smbus import BLOCK, COMMAND_SIZES_HINT, CommandSize


class MyVendorProtocol(Decoder):
    id = "my_device"
    name = "My device (SMBus)"
    stacks_on = "smbus"

    @classmethod
    def hints_for_lower(cls, opts):
        return {COMMAND_SIZES_HINT: {
            0xD0: CommandSize(write=2, read=2),        # a word register
            0xD1: CommandSize(write=None, read=BLOCK), # read-only block
        }}
```

Namespace hint keys by the decoder that reads them (`"smbus.command_sizes"`).
Hints are per run, not per frame; they can depend on options but not on the
decoded data.

## Where to put the file

The registry scans these locations in order. When two classes share an `id`,
the later one wins and a warning is logged:

1. **Built-ins:** `better_scope/decode/decoders/*.py`, for protocols that
   ship with the app.
2. **Entry points:** a pip-installable package can declare decoders in the
   `better_scope.decoders` group. Use this for private protocol packages,
   such as those under NDA, that must not live in this repo. In your
   package's `pyproject.toml`:

   ```toml
   [project.entry-points."better_scope.decoders"]
   myproto = "my_package.myproto"          # a module (all decoders in it)
   # or: myproto = "my_package.myproto:MyProtocol"   (one class)
   ```

3. **Plugin folders:** any `.py` file in `~/.better_scope/plugins/`, or in a
   folder listed in the `BETTER_SCOPE_PLUGIN_PATH` environment variable
   (separate multiple folders with `;` on Windows). Files starting with `_`
   are ignored.

A plugin that fails to import or validate is skipped and never crashes the
app. The reason goes to the log and to `decoder_registry().errors`. To
re-scan after editing a plugin, call `decoder_registry(refresh=True)`.

## Testing with synthetic signals

`tests/signals.py` builds realistic **analog** test waveforms, with edge time,
noise, DC offset and a chosen oversampling ratio, so your decoder is tested
through the real digitizer:

```python
from better_scope.decode import BusConfig, Level, run
from tests.signals import levels_waveform, uart_waveform

# Generic: (level, duration) segments -> (t, v)
t, v = levels_waveform([(True, 1e-6), (False, 3e-6), (True, 1e-6)], sample_rate=100e6,
                       edge_time=20e-9, noise=0.03)

# UART: bytes, framing options, and injected errors
t, v = uart_waveform([0x41, 0x42], 115200, parity="even", parity_errors=[1], noise=0.05)

bus = BusConfig("u", "UART", "uart", role_map={"rx": "CH1"}, options={"baud": 115200, "parity": "even"})
result = run([bus], {"CH1": (t, v)})
assert result.for_bus("u", Level.WORD)[1].error == "parity error"
```

For clocked buses, `i2c_waveform` and `spi_waveform` return one waveform per
line:

```python
from tests.signals import I2cSegment, i2c_waveform, spi_waveform

# I2C: transactions of segments (repeated START between segments), NACKs, clock stretching
scl, sda = i2c_waveform([[I2cSegment(0x40, data=[0x8B]), I2cSegment(0x40, read=True, data=[0x34, 0x12])]],
                        400e3, stretch_bits=2)

# SPI: per CS assertion, (mosi, miso) words; returns {"sclk", "mosi", "miso", "cs"}
waves = spi_waveform([[(0xA5, 0x3C)]], 1e6, mode=3, word_size=8, partial_bits=3)
```

To test a plugin file without installing it, point a `DecoderRegistry` at a
temporary folder and pass it to `run()`. `tests/test_decode_registry.py` shows
how. Always cover error cases (bad checksum, NACK, truncated capture) as well
as the happy path.

## Walkthrough: `uart.py`

[`better_scope/decode/decoders/uart.py`](../better_scope/decode/decoders/uart.py)
is the reference decoder. The key parts:

**Metadata.** Two optional roles, with `require_one_of = ("rx", "tx")` so at
least one must be mapped. The options cover the whole frame format. Only
`stop_bits` needs a float choice.

```python
roles = (Role("rx", "RX", required=False), Role("tx", "TX", required=False))
require_one_of = ("rx", "tx")
options = (
    Option("baud", "Baud rate", int, 115200),
    Option("data_bits", "Data bits", int, 8, choices=(5, 6, 7, 8, 9)),
    Option("parity", "Parity", str, "none", choices=("none", "even", "odd", "mark", "space")),
    Option("stop_bits", "Stop bits", float, 1.0, choices=(1.0, 1.5, 2.0)),
    ...
)
```

**`decode()`** validates what it can't decode past (`DecodeError` for baud
<= 0), then decodes each mapped line independently. Every frame carries
`data["role"]` so the GUI can split RX and TX into separate rows.

**Setup per line.** Check oversampling with `oversampling_warning`, apply
`invert` with `sig.inverted()`, and precompute the sample points in bit times
(0.5 for start, 1.5 + i for data, then parity and stop).

**The main loop** works like a hardware UART:

```python
while (ts := sig.next_edge(t, "falling")) is not None:
    ...                                   # break check (line low > one character)
    levels = sig.levels_at(ts + offsets_arr * bit)   # sample all bit centres at once
    if levels[0]:                         # start bit not low at its centre: glitch
        t = ts
        continue
    ...                                   # assemble value, check parity and stop
    yield from char_frames
    t = ts + (1 + nd + np_bits + 0.5) * bit   # resume from the middle of the stop bit
```

It walks edges with `next_edge` and samples in bulk with `levels_at`, so there
is no per-sample Python loop. Re-syncing on every start bit keeps long captures
aligned even with a small baud-rate error.

**Edge cases a real capture hits:**

- *Capture starts low:* wait for the first rising edge.
- *Capture starts mid-character:* until one character decodes cleanly, a
  falling edge might be a data bit. Candidates that fail framing are silently
  dropped. A first character that frames cleanly but wasn't preceded by enough
  idle gets `data["uncertain"] = True`.
- *Break:* the line is low for longer than a whole character. The decoder
  emits one `break` frame up to the rising edge and resumes there.
- *Capture ends mid-character:* the decoder emits the bits it has, plus a
  `truncated` frame carrying an `error`.

**Frames emitted:** `start` / `bit` / `parity` / `stop` at `Level.BIT`. Errors
become `parity_error` and `framing_error` frames with `error` set. A `byte` at
`Level.WORD` carries `value`, `parity_ok` and `stop_ok`, and its `error`
combines any bit errors. A `packet` at `Level.PACKET` groups characters
separated by less than `packet_gap` bit times.

**Helper:** `estimate_baud(signal)` measures the shortest pulse (ignoring
glitches under 3 samples) and snaps to a standard rate within 5%.

## Running a decode from a script

```python
from pathlib import Path

from better_scope.core import BetterScope
from better_scope.decode import BusConfig, Threshold, frames_to_csv, run

scope = BetterScope()
scope.scan_for_instruments()
scope.auto_setup_scope()   # last-used or first compatible MSO
waves = scope.acquire_waveforms(["CH1", "CH2"])

bus = BusConfig(
    bus_id="dbg",
    name="Debug UART",
    decoder_id="uart",
    role_map={"rx": "CH1", "tx": "CH2"},
    thresholds={"CH1": Threshold("manual", 1.65)},
    options={"baud": 115200, "display": "ascii", "packet_gap": 5},
)
result = run([bus], waves)
for w in result.warnings:
    print(w)
frames_to_csv(result, Path("uart_frames.csv"), levels=[1, 2])
```

`BusConfig` round-trips through JSON (`to_json()` / `from_json()`), which is
how the app stores bus setups in `~/.better_scope/config.json`.
