# Better_Scope

A DearPyGui desktop tool for Tektronix oscilloscopes: scan/auto-connect, name
channels, plot live waveforms, and capture screenshots. Built on the
[AlphaOmegaSemiconductor pymeasure fork](https://github.com/AlphaOmegaSemiconductor/pymeasure),
which ships tested Tektronix MSO drivers (`MSO44`, `MSO54`, `MSO58`).

Inspired by the earlier Simple Scope app; reworked onto DearPyGui, the pymeasure
fork, and uv.

## Requirements

- Python 3.14 (3.15 once DearPyGui ships a cp315 wheel)
- [uv](https://docs.astral.sh/uv/) for environment and dependency management

## Setup

```sh
uv sync
```

## Run

```sh
uv run python main.py
# or, via the console script:
uv run better-scope
```

On launch the app scans for VISA instruments and auto-connects to the last-used
or first compatible Tektronix MSO. The **Channels** and **Plot** tabs build
themselves from the connected model's channel count.

## Test

```sh
uv run pytest
```

Tests run without hardware (VISA discovery and the pymeasure driver are mocked).

## Project layout

- `better_scope/core.py` — GUI-agnostic backend (`BetterScope`); usable standalone.
- `better_scope/instruments/` — VISA discovery and the pymeasure driver wrapper.
- `better_scope/gui/` — DearPyGui app shell, worker thread, and tabs.
- `better_scope/decode/` — GUI-agnostic software serial decoder with a plugin
  API (UART, I2C, SMBus, PMBus, SPI). See [docs/DECODERS.md](docs/DECODERS.md) to write a decoder.
  Register maps (Excel) label SMBus/PMBus/SPI traffic as register reads and
  writes; see [docs/REGISTER_MAPS.md](docs/REGISTER_MAPS.md). The **Decode**
  tab configures buses; results show as plot lanes and in the Event Table.
- `better_scope/waveform_io.py` — save/load waveforms (`.npz`, Tektronix CSV,
  generic CSV) for offline decode.
- `main.py` — entry point.
