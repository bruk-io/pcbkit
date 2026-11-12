# Worked example: my-board, one design session

A generic board, made up to show the patterns. Each change below landed in one place
(the body text, a table, a caption, or the design) and not in the others. The review is
what this skill produces at the next gate.

## What changed during the session

- Power input: a barrel jack for 9-24 V became a 2-pin terminal block for 12-24 V.
- Power: a bought 5 V regulator module became an on-board buck. The module's power-good
  LED went with it.
- Sensor port: one 6-pin connector became a 4-pin I2C port and a 2-pin analogue input.
- Layout: the connectors moved to two edges; a diagram caption still says "ports on
  three sides".
- Power button: changed from "switches the input current" to "drives the buck's enable;
  it carries a signal, not the load current".

## Claims (excerpt)

| # | Claim | Type | Design evidence | Status |
|---|---|---|---|---|
| 1 | Input on a 2-pin terminal block | mechanical | J1 footprint `TerminalBlock` 2 pads (design.py) | matches |
| 2 | Input range 9-24 V (port map table) | mechanical | body text says 12-24 V; `SUPPLY_LIMITS` row "input range" 12-24 V, passed | differs |
| 3 | 5 V from a regulator module (power section) | behavioural | no module in design.py; U2 buck on `+5V` | differs |
| 4 | Power-good LED on the 5 V rail | behavioural | no LED on `+5V` or U2's PG pin; PG has a no-connect flag | missing in design |
| 5 | Sensor port is one 6-pin connector (port map) | mechanical | J8 4-pin `+3V3/GND/SDA/SCL`, J9 2-pin `AIN/GND` | differs |
| 6 | Sensor port split into I2C and analogue (body text) | mechanical | J8, J9 as above | matches |
| 7 | Ports on three sides (diagram caption) | mechanical | layout.py: J1-J5 on the left edge, J8-J9 on the top | differs |
| 8 | Power button switches the input (port map note) | behavioural | listing: `PWR_EN` holds J3 and U2 only; `VIN` holds J1, F1 and U2, not J3 | differs |
| 9 | Power button carries a signal (body text) | behavioural | same listing as 8 | matches |
| 10 | Open question: one rail or two? | behavioural | spec still lists it; the design has `+5V` and `+3V3` | differs |
| 11 | (none) | - | the part listing has D5, a TVS on `VIN`, mentioned nowhere | not in spec |
| 12 | Bus devices U6 at 0x48 | mechanical | `test_i2c_addresses_unique_per_bus` records U6 0x48 | matches |

Claim 10 is an open question the design has answered: `differs` until the spec says so.

## Decisions needed

1. The input range is 12-24 V in the body and 9-24 V in the port map. Update the table to
   12-24 V, or go back to 9 V (U2's minimum input would not allow it)?
2. The power section still describes a module. Rewrite it for the on-board buck, or go
   back to the module?
3. The power-good LED is promised but gone. Drop it from the spec, or add it back on U2's
   PG pin?
4. The port map shows one 6-pin sensor connector. Update it to J8 and J9, or merge them?
5. The caption describes three sides. Rewrite it, or is the old layout still intended?
6. The port map note says the button switches the input. Update the note, or route the
   input current through the button?
7. "One rail or two" is open in the spec and answered in the design. Close it with two?
8. D5 is not in the spec. Add it under input protection, or remove it?

## Moves to code

- Claims 1, 5, 6: `PIN_NETS` rows for J1, J8 and J9 (proposed table, `add-check`).
- Claim 7: `PORT_EDGES` (proposed).
- Claim 4 once dropped: an `EXCLUSIONS` row (proposed).
- Claim 11: `PIN_NETS` covering every connector, and a `POWER_PATHS` row for `VIN`,
  would have shown D5 as untraced.
