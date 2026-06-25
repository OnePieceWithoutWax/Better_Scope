# Better_Scope Help

Better_Scope controls Tektronix oscilloscopes through the AlphaOmegaSemiconductor
pymeasure fork.

## Workflow

1. **Scope** — Press *Scan for Scope*. The app discovers VISA instruments and
   auto-connects to the last-used or first compatible Tektronix MSO. You can also
   pick an instrument from the list and press *Connect Selected*.
2. **Channels** — Once connected, this tab lists one row per analog channel
   (count depends on the model: MSO44/54 = 4, MSO58 = 8). Edit a channel's
   **name/label**, **scale**, **offset**, **position**, and **label X/Y**
   placement, then press *Apply Changes*. Every write is verified by reading the
   value back; mismatches are reported and logged. *Refresh* re-reads the scope.
3. **Trigger** — Configure the A-trigger **mode**, **type**, **edge source**,
   **slope**, **coupling**, and **level**, then *Apply* (also verified by
   read-back). *Force Trigger* triggers immediately; *Set 50% Level* centers the
   level; live trigger **state** and **frequency** are shown.
4. **Plot** — Tick the channels to acquire, then press *Acquire* for a single
   shot, or enable *Auto-refresh* (with a Hz rate) for continuous updates.
5. **Capture** — Save a screenshot. *Basic* takes a directory and filename;
   *Engineering* additionally builds subdirectories (e.g. IC Part Number / Test).
   Use the *+ / -* buttons to add or remove fields in a subdirectory row.
6. **Config** — Filename options (auto-increment or datestamp, mutually
   exclusive), clipboard auto-copy, and captured-image display options.

## Notes

- Filenames support *auto increment* (`_001`, `_002`, ...) or a *datestamp*
  (`_YYYY.MM.DD_HH.MM.SS`); the two are mutually exclusive.
- VISA transport uses `pyvisa-py` by default; install NI-VISA for broader
  hardware support.
