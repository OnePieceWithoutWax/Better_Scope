# 08 -- Intel SVID decoder (gated)

Read `prompt_que/README.md` first. Requires prompts 02 and 10.

## Gate -- resolve before writing code

The user asked for SVID "provided it is not private or protected". Status:
- Intel's SVID protocol specification (VR12/VR13/VR14, IMVP) is distributed
  under Intel NDA. Its frame timing and field layout are **not** publicly
  published.
- What *is* public: vendor VR datasheets list SVID command codes and register
  addresses. For example, TI TPS544C26 documents SetVID_Fast (01h), SetVID_Slow
  (02h), SetVID_Decay (03h), SetPS (04h), SetRegAddr (05h), SetRegData (06h),
  GetReg (07h), and SetWP (09h). Intel patents (e.g. EP2802994B1) describe the
  frame in general terms.
- Tektronix sells an SVID decode option, which is useful for cross-checking if
  a licensed scope is available.

User selected this option:
1. **Private plugin (recommended if they hold the Intel spec):** implement SVID
   in the private plugin package from prompt 10, using the licensed spec. Nothing
   goes in this repo.

## If implemented (either path)
- Roles: `clk` (SVID clock), `data` (SVID data), and optionally `alert`.
- Frames: start, address (VR id), command (named from the table above),
  payload, parity (checked), VR ACK/NACK, end, and GetReg read-back data.
- Stacked semantic layer: VID codes to volts via a selectable VID table
  (VR12/VR13 step tables go with prompt 09's VID-table work). Register
  addresses for GetReg/SetRegData can bind to a register map through prompt 03's
  mapper (device = VR address).
- Tests: synthetic SVID waveforms built from the same declared frame table,
  including parity error and NACK.
