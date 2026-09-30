# Follow-up: what still needs verifying

Things built in prompts 00-07 that were only tested on synthetic data, were
built on assumptions, or wait on something from you. Local notes only; do not
commit this file.

## 1. Sample files needed from you

Put them in `resources/examples/`.

- [ ] **Tektronix CSV, one channel**, saved on the 4/5/6 Series scope.
  - The reader (`better_scope/waveform_io.py`) only assumes a block of
    `key,value` header rows followed by a `TIME,...` row. It has only been
    tested on a made-up file (`TEK_CSV` in `tests/test_waveform_io.py`).
  - Check that the header lands in `meta["header"]`, units come from
    `Vertical Units`, and time/voltage match what the scope shows.
- [ ] **Tektronix CSV, several channels** (for example CH1-CH4 in one file).
  - Check the column names and the per-channel units.
  - Check for trailing commas or empty columns.
  - Check how long a long record takes to load.
- [ ] **Tektronix `.wfm`** (one or more channels, with the firmware version noted).
  - The reader is not implemented; loading a `.wfm` currently shows an error.
  - Needs the version string in the file (for example `WFM#003`), checked
    against Tek's reference waveform file-format document.
- [ ] **A real Excel register map** from a device you use.
  - The importer (prompt 03) was built on the default template schema only.
    Its column/sheet aliases still need adjusting to match real maps.

## 2. Needs a real scope

- [ ] **Acquire + decode**
  - Channels needed by enabled buses are acquired even when not ticked.
  - Decode runs after each acquisition.
  - The status line shows warnings (undersampling, missing channel).
- [ ] **Time axis**
  - pymeasure builds time as `arange(n) * x_incr + x_zero` and ignores
    `pt_offset`. Check that the trigger point and the alignment between
    channels match the scope display.
- [ ] **Long records**
  - Check transfer plus decode time for multi-million-sample records.
  - Check that zoom/pan stays responsive, and that auto-refresh with decode
    doesn't pile up (only the latest pending decode should run).
- [ ] **Save waveforms with each capture** (Config tab)
  - The `.npz` next to the screenshot should contain exactly the channels
    turned on on the scope (`SELECT:CHn?` read through `channel.enable`).
  - Reloading it should reproduce the decode.
- [ ] **Units**
  - Native `.npz` files label every source `V`. Check with a current probe
    whether the real unit (A) should be read from the scope and saved.
- [ ] **pymeasure return shape**
  - `get_multiple_waveforms` should return numpy arrays for every source.
  - Check MATH/REF sources if they will be used.

## 3. Check by hand in the GUI

These were only exercised from a script that drove the app and saved
screenshots. Nobody has clicked through them yet.

- [ ] **File dialogs**
  - Save: a typed name without an extension gets `.npz`/`.csv` from the
    selected filter; a typed extension isn't doubled.
  - Load: the combined "Waveform files" filter shows `.npz`, `.csv`, `.txt`
    and `.wfm`.
  - Exports and the map template save to the capture save directory by default.
- [ ] **Loading a file with saved bus configs**
  - The Apply / Keep current popup appears.
  - Apply replaces the buses and maps them to the `FILE:` sources.
- [ ] **Decode tab editing**
  - Every widget saves immediately: rename, enable/disable, decoder change
    (clears roles/options that no longer apply), source dropdowns.
  - Threshold: auto/manual, level, and hysteresis (0 = auto) all change the
    decode.
  - Option tooltips appear.
- [ ] **SPI register-map binding form**
  - Edit the frame layout fields and confirm the register labels in the lanes
    and Event Table. So far this is only tested in the backend.
- [ ] **Event Table**
  - Paging with more than 500 rows.
  - Bus/device/register/errors-only filters.
  - Clicking a row centres (and zooms) the Plot tab.
  - The events export writes only the filtered rows; confirm that is what
    you want.
- [ ] **Plot lanes**
  - Labels are readable.
  - Error frames are red.
  - Dense lanes merge boxes when zoomed out.
  - Level and bus toggles work.
  - The X range is kept when lanes are rebuilt.
- [ ] **Plugins section**
  - Put a broken `.py` in the plugin folder and confirm the error is listed.
  - Rescan picks up a new plugin.

## 4. Known limitations to confirm are acceptable

- [ ] **SMBus without size hints:** a Block Write with count 1 looks like a
  Write Word, and count 3 like a Write 32. PEC auto cannot catch a bad PEC
  without hints.
- [ ] **Manufacturer PMBus commands:** a user register map does not yet feed
  command sizes back to SMBus (no PEC placement for MFR commands).
- [ ] **PMBus command names** come from PMBus Part II rev 1.3.1. Check them
  against the devices you use (C4h-FDh are MFR_SPECIFIC).
- [ ] **UART:** the first character is marked "sync uncertain" when the
  capture starts mid-stream.
- [ ] **Register map addresses:** the SMBus mapper trusts the SMBus
  classification. A 2-byte payload to a byte register is flagged as a size
  mismatch.
- [ ] **Startup:** numpy was already loaded at startup before this work (by
  dearpygui/pyvisa). Only the decode backend, plotting and openpyxl are
  deferred.

## 5. Waiting on outside blockers (from the prompt queue)

- [ ] **00:** full move to Python 3.15 `lazy import` once DearPyGui ships a
  cp315 wheel.
- [ ] **06 (live decode):** built; hardware checks are in section 6.
- [ ] **07 (digital channels):** built; TLP058 checks are in section 7.
- [ ] **08 (SVID):** the Intel spec is under NDA.
- [ ] **10 (SVI3):** stays a stub until the spec details are supplied.

## 6. Live decode (prompt 06) on a real MSO4/5/6

Only tested against a fake instrument with scripted STATE/NUMACq. Use a
real bus (UART or I2C) mapped on the Decode tab, then the Plot tab's
*Live decode* row.

- [ ] **Stopped trigger:** with *Live decode* on, press Single on the front
  panel several times. Each press should give exactly one decode, including
  when NUMACq reads 1 every time.
  - Known gap: a Single that starts and finishes within one 0.3 s poll, with
    the same NUMACq as last time, is not seen. Check how often that happens
    with a fast trigger; *Arm single* from the app does not have this gap.
- [ ] **No decode on unchanged NUMACq:** leave the scope stopped. No
  repeated decodes should happen. Changing a setting while stopped resets
  NUMACq and triggers one extra decode of the same data; confirm that is
  acceptable.
- [ ] **Decode while running:** turn it on with the scope running.
  - The scope stops, transfers, and runs again after every decode.
  - The skipped counter grows while decoding.
  - Check whether RUN resets NUMACq to 0. The skipped count is exact if it
    does, and can undercount if it does not.
- [ ] **Do channels mix acquisitions?** Turn *Stop for transfer* off with
  the scope running and a fast-changing signal on two channels (for example
  a counter on SPI MOSI and CS). Check whether CH1 and CH2 still come from
  the same acquisition. If they do, the STOP isn't needed; record the result.
- [ ] **Arm single:** press it with live off (one decode, then Stop After
  goes back to Run/Stop), and with live on (keeps re-arming). Turning live
  off restores Stop After.
- [ ] **Per-cycle time:** note transfer and decode times from the status
  row for a short record (10k) and a long one (10M) to record in the summary.
- [ ] **Cancel:** turn live off in the middle of a long transfer. The scope
  is left running (if it was), and there are no stray decodes afterwards.
- [ ] **Auto-refresh** now only plots and pauses while live is on. Check
  that this is what you want.

## 7. Digital channels (prompt 07) with a TLP058

Built from memory of the 4/5/6 Series programmer manual. No manual is in
the repo. Record the manual revision you check against.

- [ ] **Detection:** `CH<x>:PROBETYPE?` returns `DIGITAL` for a TLP058 (read
  on connect through the fork's `channel.probe_type`). Reconnect after
  moving a probe; there's no rescan button.
- [ ] **Source names:** `DATa:SOUrce CH<x>_DALL` is accepted, and the bits
  are `CH<x>_D0`..`D7`.
- [ ] **Encoding:** the transfer sets `DATa:ENCdg SRPbinary` and puts the old
  value back. Check that SRPbinary is accepted for DALL.
- [ ] **Sample width:** check `WFMOutpre:BYT_Nr?` for DALL (1 or 2).
- [ ] **Bit order:** bit n is assumed to be D<n>, taken from the low byte if
  2 bytes are returned. Put a known pattern on D0 and D7 and compare the
  plot with the scope display.
- [ ] **Time base:** digital and analog traces line up with the trigger
  point, using `XZEro + n * XINcr` as for analog.
- [ ] **Decode:** a UART and an SPI decode from D bits give the same result
  as the scope display.
- [ ] **Display state:** save-with-capture includes a digital channel's 8
  bits when `SELect:CH<x>` is on. Check that this query reflects whether the
  digital group is shown, or whether `DISplay:WAVEView1:CH<x>_DALL:STATE` is
  needed instead.
- [ ] **Threshold** (optional in the prompt, not built): decide whether the
  app should read or write the digital threshold (`CH<x>_D<n>:THReshold`).
  Confirm the command name first.
- [ ] **Upstream:** the digital transfer path is a candidate for the AOS
  pymeasure fork. Its `get_curve_data` reads the block header as text and
  assumes signed ints.
- [ ] **Plot checks by hand:** there are 8 source checkboxes per digital
  channel, and many ticked bits squeeze the digital subplot. Check whether
  that's usable.
