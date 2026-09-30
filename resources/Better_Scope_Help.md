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
   shot, or enable *Auto-refresh* (with a Hz rate) for continuous plotting
   (auto-refresh does not decode; see *Live decode* below).
   Long records are drawn min/max decimated to the plot width; zoom in for
   full detail. *Save waveforms...* writes `.npz` (native, keeps the decode
   setup) or `.csv`; *Load waveforms...* reads `.npz`, Tektronix CSV or a
   generic CSV (time column, then one column per source). Loaded sources
   appear as `FILE:CH1`, ... and work with no scope connected.
5. **Capture** — Save a screenshot. *Basic* takes a directory and filename;
   *Engineering* additionally builds subdirectories (e.g. IC Part Number / Test).
   Use the *+ / -* buttons to add or remove fields in a subdirectory row.
6. **Config** — Filename options (auto-increment or datestamp, mutually
   exclusive), clipboard auto-copy, captured-image display options, and
   *Save waveforms (.npz) with each capture* (saves the channels shown on the
   scope next to the screenshot).

## Decode

Serial buses are decoded in software from acquired or loaded waveforms (no
scope decode license needed).

1. **Decode tab** — *Add* a bus, pick a decoder (UART, I2C, SMBus, PMBus,
   SPI, or a plugin), map each signal role to a source (`CH1`... or a loaded
   `FILE:` source), and set the threshold per source (*auto* uses the signal's
   midpoint; *manual* uses *Level*; hysteresis 0 = automatic). The options
   form comes from the decoder. Buses can be renamed, duplicated, disabled or
   removed; everything is remembered between sessions.
2. **Register maps** — For SMBus/PMBus and SPI buses, *Add Excel map...* binds
   a register map (optionally at a specific SMBus address; SPI buses also set
   the frame layout). Map errors are shown under the binding. PMBus buses use
   the standard command names when no map is bound. *Save map template...*
   writes an example workbook.
3. **Run** — Every *Acquire* decodes the enabled buses (their channels are
   acquired automatically). *Decode* acquires and decodes in one step, or
   re-decodes the loaded file when no scope is connected. Warnings (e.g.
   undersampling, a missing channel) appear in the status line and the
   *Warnings* list.
4. **Live decode** (Plot tab) — Polls the scope's acquisition state and
   transfers + decodes each new acquisition: when the scope stops (Single or
   Stop on the front panel), and, with *Decode while running*, while it runs
   (*Stop for transfer* stops the scope during the transfer so every channel
   comes from one acquisition, then runs it again). *Arm single* sets Stop
   After = Sequence and runs; with *Live decode* on it re-arms after each
   decode. Only one transfer + decode runs at a time; acquisitions missed
   meanwhile are counted as skipped. The row shows the last decode time,
   transfer and decode durations. Expect it to be slow on long records.
5. **Plot lanes** — Each bus gets a lane under the waveforms, with rows for
   bits, bytes/words, transactions and register accesses; toggle levels and
   buses on the *Decode:* row. Errors are drawn in red.
6. **Event Table** — The register command history (time, bus, device, R/W,
   register, value, fields, error) with bus/device/register/errors-only
   filters. Click a row to centre the plot on it.
7. **Export** — *Export events CSV* writes the rows passing the table's
   filters; *Export frames CSV* writes every frame of the shown levels and
   buses.
8. **Plugins** — The Decode tab's *Plugins* section lists the decoders found
   and any plugin that failed to load.

Writing a decoder: see `docs/DECODERS.md`. Register-map format: see
`docs/REGISTER_MAPS.md`.

## Notes

- Filenames support *auto increment* (`_001`, `_002`, ...) or a *datestamp*
  (`_YYYY.MM.DD_HH.MM.SS`); the two are mutually exclusive.
- VISA transport uses `pyvisa-py` by default; install NI-VISA for broader
  hardware support.
