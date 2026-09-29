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
