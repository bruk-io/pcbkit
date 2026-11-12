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

## Written down before the design was read

```json
{
  "part.J1.footprint": "Connector_BarrelJack:BarrelJack_Horizontal",
  "parts.connector": ["J1", "J2", "J3", "J4", "J5", "J8"],
  "part.J8.pads": 6,
  "net.PWR_EN": ["J3.1", "U2.4"]
}
```

`spec-claims.json` took the port map table as it stood. Where the body text says
otherwise (claims 5 and 6), that is a finding before the design is read. `SUPPLY_LIMITS`
gained the port map's "input range 9-24 V" row, and `PORT_EDGES` the caption's three
sides.

## Claims (excerpt)

| # | Claim | Type | Design evidence | Status |
|---|---|---|---|---|
| 1 | Input on a barrel jack (port map) | mechanical | `listing.py`: `part.J1.footprint` claimed a barrel jack, design `TerminalBlock` | differs |
| 2 | Input range 9-24 V (port map table) | mechanical | `test_supply_pins_in_range`: the "input range" row failed, U2's minimum input is 12 V | differs |
| 3 | 5 V from a regulator module (power section) | behavioural | listing: no module in `parts.other`; U2 buck on `net.+5V` | differs |
| 4 | Power-good LED on the 5 V rail | behavioural | listing: no LED on `net.+5V` or U2's PG pad; PG has a no-connect flag | missing in design |
| 5 | Sensor port is one 6-pin connector (port map) | mechanical | `listing.py`: `part.J8.pads` claimed 6, design 4; `parts.connector` lacks J9 | differs |
| 6 | Sensor port split into I2C and analogue (body text) | mechanical | listing: J8 4 pads `+3V3/GND/SDA/SCL`, J9 2 pads `AIN/GND` | matches |
| 7 | Ports on three sides (diagram caption) | mechanical | `PORT_EDGES` check: J8 and J9 failed their "right" edge | differs |
| 8 | Power button switches the input (port map note) | behavioural | `listing.py`: `net.PWR_EN` matches J3 and U2 only; `net.VIN` holds J1, F1 and U2, not J3 | differs |
| 9 | Power button carries a signal (body text) | behavioural | same listing as 8 | matches |
| 10 | Open question: one rail or two? | behavioural | spec still lists it; the listing has `net.+5V` and `net.+3V3` | differs |
| 11 | (none) | - | the listing has D5, a TVS on `net.VIN`, that no claim names | not in spec |
| 12 | Bus devices U6 at 0x48 | mechanical | the `I2C_EXPECTED` row from the spec passed | matches |

Claim 10 is an open question the design has answered: `differs` until the spec says so.

## Decisions needed

1. J1 is a terminal block in the design and a barrel jack in the port map. Update the
   port map, or change J1 back?
2. The input range is 12-24 V in the body and 9-24 V in the port map. Update the table to
   12-24 V, or go back to 9 V (U2's minimum input would not allow it)?
3. The power section still describes a module. Rewrite it for the on-board buck, or go
   back to the module?
4. The power-good LED is promised but gone. Drop it from the spec, or add it back on U2's
   PG pin?
5. The port map shows one 6-pin sensor connector. Update it to J8 and J9, or merge them?
6. The caption describes three sides. Rewrite it, or is the old layout still intended?
7. The port map note says the button switches the input. Update the note, or route the
   input current through the button?
8. "One rail or two" is open in the spec and answered in the design. Close it with two?
9. D5 is not in the spec. Add it under input protection, or remove it?

## Written down once decided

- Claims 1, 5, 6: `PIN_NETS` rows for J1, J8 and J9 (proposed table, `add-check`), and
  `spec-claims.json` updated to the decided footprint and pad counts.
- Claims 2, 7: the `SUPPLY_LIMITS` row and `PORT_EDGES` changed to the decided values.
- Claim 4 once dropped: an `EXCLUSIONS` row (proposed).
- Claim 11: `PIN_NETS` covering every connector, and a `POWER_PATHS` row for `VIN`,
  would have shown D5 as unaccounted for.
