# 00 -- Move to Python 3.15+ and apply PEP 810 lazy imports

Read `prompt_que/README.md` first.

## Goal

Change `pyproject.toml` to `requires-python = ">=3.15"` and make every heavy
import lazy (PEP 810), so startup only loads what the first frame needs.

## Blocker -- check this before changing anything

As of 2026-09-29:
- Python 3.15.0 final is scheduled for 2026-10-01 (rc2 is out). `uv python
  list` on this machine only offered 3.15.0a8, so run `uv python install 3.15`
  and confirm it resolves to a final or rc build.
- **DearPyGui 2.3.1 ships wheels for cp38-cp314 only, with no cp315 wheel and
  no usable sdist.** Building it from source on Windows is not practical. This
  blocks the move to 3.15.
- `pyyaml` has no cp315 wheel yet. It builds from sdist in pure-Python mode,
  which is fine.
- Confirmed cp315 win_amd64 wheels: numpy 2.5.3, pillow 12.3.0, pywin32 312.
  pyvisa, pyvisa-py and openpyxl are pure Python.
- The pymeasure fork (git source) is unverified on 3.15. Run the test suite.
- `tm_data_types` (Tektronix) is capped at `<3.14` and pulls numba/scipy.
  Don't use it (see prompt 05).

Step 1: re-check PyPI for a DearPyGui release with a `cp315-win_amd64` (or
abi3) wheel:

```
curl -s https://pypi.org/pypi/dearpygui/json
```

- **If a cp315 wheel exists:** do the full migration below.
- **If not:** do not change `requires-python`. Take the fallback, then stop and
  report to the user. Ask whether to stay on 3.13 or move to 3.14 (DearPyGui
  has cp314 wheels) in the meantime.

## Full migration (cp315 DearPyGui available)

1. `requires-python = ">=3.15"`. Update README "Requirements". Re-lock with
   `uv lock` and `uv sync`. Check that `run_app.bat` / `run_app.ps1` still work
   (invoke `shell-script-rules` before editing them).
2. Measure baseline startup first: `uv run python -X importtime main.py`
   (close the window right away), and record the top cumulative imports in
   PROGRESS.md.
3. Convert heavy module-level imports to `lazy import x` / `lazy from x import
   y`: numpy, pyvisa, pymeasure, PIL, win32clipboard/win32con, and every new
   heavy dependency the decoder adds (openpyxl, yaml, systemrdl...). Keep
   `dearpygui` eager, since the first frame needs it.
   - PEP 810 limits: `lazy` is only valid at module level, not inside
     `try`/`except`, functions, or `with` blocks, and not for `from x import *`.
     Move function-local imports (e.g. in `core.copy_to_clipboard`, `drivers.driver_for`)
     to module-level `lazy` imports where that reads cleaner. Keep them local
     if that is clearer.
   - `ImportError` now surfaces on first use, not at import. Check
     `copy_to_clipboard`'s `except ImportError` still catches it where it's
     reified.
4. Re-measure with `-X importtime` and record before/after in PROGRESS.md and
   the session summary.
5. `uv run pytest` passes. Launch the app and confirm scan/connect still work.

## Fallback (no cp315 DearPyGui)

- Keep `requires-python` as is.
- Add a module-level `__lazy_modules__ = [...]` listing the heavy modules in
  each file that imports them. It is ignored on <3.15 and becomes lazy
  automatically on 3.15. Code stays valid on the current interpreter.
- Record in `prompt_que/README.md` (status column) that 00 is on fallback and
  what to re-run once DearPyGui supports 3.15.

## Acceptance

- Either: 3.15 + `lazy` imports + measured startup improvement, tests green.
- Or: fallback applied, tests green, user asked about 3.13 vs 3.14.
