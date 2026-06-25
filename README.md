# Better_Scope

A DearPyGui desktop tool for Tektronix oscilloscopes: scan/auto-connect, name
channels, plot live waveforms, and capture screenshots. Built on the
[AlphaOmegaSemiconductor pymeasure fork](https://github.com/AlphaOmegaSemiconductor/pymeasure),
which ships tested Tektronix MSO drivers (`MSO44`, `MSO54`, `MSO58`).

Inspired by the earlier Simple Scope app; reworked onto DearPyGui, the pymeasure
fork, and uv.

## Requirements

- Python 3.13
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
- `main.py` — entry point.
