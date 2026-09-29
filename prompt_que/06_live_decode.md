# 06 -- Live decode on new acquisition / stopped state

Read `prompt_que/README.md` first. Requires prompt 04.

## Goal

An opt-in "Live decode" mode that re-acquires and re-decodes when the scope
has a **new acquisition**, not on a timer. It is expected to be slow on long
records, and that's accepted. Single capture remains the main mode.

## Behaviour
- Poll cheaply on the worker thread: `ACQuire:STATE?` and `ACQuire:NUMACq?`
  (add these to the backend. Check whether the pymeasure fork's acquisition
  subsystem already exposes them, and use it if so).
- Trigger a transfer + decode when either:
  - the scope is **stopped** (STATE 0) and NUMACq changed since the last decode.
    This covers the user pressing Single/Stop on the front panel.
  - the scope is **running**, NUMACq advanced, and the user enabled "decode
    while running". Then: STOP, transfer all needed channels (so they come
    from the same acquisition), decode, and RUN again. Verify on hardware
    whether channels transferred while running can mix acquisitions. If
    they can't, the STOP isn't needed; document what you found.
- Optional "Arm single": sets `ACQuire:STOPAfter SEQuence` and
  `ACQuire:STATE RUN`, waits for the stopped state, decodes, and re-arms if live
  mode is on.
- Only one acquire/decode runs at a time. Skip triggers while busy, and show a
  counter of skipped acquisitions. Stopping live mode cancels cleanly.
- Show the last decode time and duration in the status line.
- Replace or merge with the Plot tab's timer auto-refresh. Keep the timer
  for plain plotting if it's still useful, but it must not decode.

## Tests
Hardware-free: a fake instrument whose STATE/NUMACq sequence is scripted,
asserting when decodes happen (stopped with a new acquisition, running with
decode-while-running, no decode on an unchanged NUMACq, and skip while busy).

## Verify on hardware
Ask the user to run it against an MSO4/5/6 with a real bus. Record the observed
per-cycle time (transfer vs decode) in the summary.
