# 09 -- More register-map formats, PMBus data formats, and encodings

Read `prompt_que/README.md` first. Requires prompt 03. This prompt collects the
"extra considerations" deferred from the simplest-first v1. Split it across
sessions if it gets large (suggested: 09a formats, 09b PMBus encodings, 09c
multi-byte/paging).

## Before starting
Ask the user which items below they actually need now, in priority order.
Their real register map (supplied for prompt 03) should drive the choice.

## 09a -- Import formats (each is an importer via the `better_scope.regmap_importers` registry)
- **Native YAML/JSON** (PyYAML): a documented schema mirroring
  `regmap/model.py`, plus an "Export map as YAML" action, so Excel maps can be
  converted and diffed in git.
- **SystemRDL 2.0** via `systemrdl-compiler`, and **IP-XACT** via
  `peakrdl-ipxact` (both import into the systemrdl model; convert that to
  ours). Check that both install on the project's Python version first.
- **CMSIS-SVD** via the `cmsis-svd` package, or a direct XML parse if the
  package fights the Python version.
- **CSV**: the same columns as the Excel template.
- Tests: one small fixture per format that decodes to the same `Device` as the
  Excel template fixture.

## 09b -- PMBus data formats
- **Linear11** (5-bit signed exponent, 11-bit signed mantissa), used by most
  READ_* telemetry.
- **Linear16** for VOUT-related commands, with the exponent taken from
  **VOUT_MODE**. This needs context that the "no state tracking" rule excludes.
  Ask the user to choose between: (a) a per-device configured VOUT_MODE
  exponent, (b) a minimal decode *context* that only remembers VOUT_MODE and PAGE
  from earlier in the same capture (not register state), or (c) both, with (a)
  as the fallback.
- **VID format** (VOUT_MODE mode = VID) with selectable VID tables: VR12 (5 mV),
  VR12.5 (10 mV), VR13, IMVP9, and AMD tables as available. Share the tables
  with SVID (prompt 08).
- **DIRECT format** (m, b, R coefficients), configured per command in the map.
- Show engineering values with units in the event table alongside the raw hex.

## 09c -- Addressing and layout
- **Paging:** PMBus `PAGE` (0x00) selects a rail. Maps can define per-page
  registers or mark registers as paged. Same context question as VOUT_MODE.
- **Multi-byte registers and byte order:** per-device and per-register
  little/big endian, and registers wider than the transfer size (spread over
  auto-incremented addresses).
- **I2C (non-SMBus) devices** with 8/16-bit register-address pointers and
  auto-increment on multi-byte reads/writes.
- **Field value transforms:** signed fields, `value = raw * lsb + offset` with
  units, and access types (RO/WO/W1C) flagged when violated, e.g. a write to a
  RO register.
- **Multiple devices per bus**, each with its own map, keyed by address or
  SPI layout.
- **Write-mask display:** for writes, show which fields changed vs the reset
  value (not vs tracked state).

## Out of scope
Full register state tracking over the capture (the user explicitly doesn't want it).
