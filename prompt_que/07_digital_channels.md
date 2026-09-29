# 07 -- Digital channel (FlexChannel / TLP058) support

Read `prompt_que/README.md` first. Requires prompts 01 and 04.

## Goal

Let bus roles map to digital probe bits (e.g. `CH1_D0`..`CH1_D7`) on MSO4/5/6
FlexChannels fitted with a TLP058 logic probe, and decode them with no
thresholding step.

## Work
1. **Detect digital channels:** find which FlexChannels have a digital probe
   (check the programmer manual for the probe-type query, e.g. `CH<x>:PROBETYPE?`
   or `DISplay:WAVEView1:CH<x>_DALL:STATE`), and list the available `CHx_Dn` sources.
2. **Transfer:** `DATa:SOUrce CHx_DALL` returns all 8 bits per sample. Work out
   the encoding and byte width from `WFMOutpre?` and split it into 8 boolean
   arrays (numpy bit ops). The time base comes from the preamble, the same as
   analog. The pymeasure fork's `get_scaled_waveform` assumes analog scaling, so
   add a digital transfer path in Better_Scope (raw `write`/`read_binary_values`
   on the instrument). Note in the summary that it's a candidate to upstream to
   the AOS pymeasure fork. Don't edit the fork from this repo.
3. **Decode:** build `LogicSignal` straight from the boolean arrays. Role
   dropdowns list digital bits alongside analog channels. Threshold controls are
   hidden for digital sources (threshold is set on the scope; optionally
   expose `CH<x>:DIGital:THReshold` read/write).
4. **Plot:** draw digital bits as stacked logic traces (the Plot tab already
   has subplots from prompt 04).
5. **Save/load (prompt 05):** the native format stores digital sources packed
   (`np.packbits`).

## Tests
Hardware-free: a fake `CHx_DALL` byte payload is split into the correct bits,
and a UART/SPI decode runs from boolean sources.

## Verify on hardware
Ask the user to test with a TLP058 fitted. Confirm the query names against the
MSO programmer manual, and record the manual revision used.
