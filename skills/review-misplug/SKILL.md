---
description: >-
  Review a pcbkit board and its enclosure for what hands, cables and tools do to it -
  every connector, button and user-reachable part checked for reversed, wrong-port,
  offset-by-one, hot-plugged, left-unplugged and tool-shorted mating, plus finger reach,
  cable routing, labels and pull-out loads - and write each port's pinout and its plugs
  into specs.py so the pin-landing questions become a check. Run it after placement and
  before ordering; load it before reviewing, planning or advising on connector choice,
  pinouts, port layout or enclosure cut-outs, even for one connector. Without it Claude
  checks only the intended mating, trusts family or pin count to stop a wrong-port mate,
  misses a narrower plug seated offset in a wider socket, and reaches for protection
  parts or labels before changing the family, keying or pin order.
---

# Mis-plug and physical usability review

Scope: what hands, cables and tools do to the board. Gate: after `pcbkit build`, before
ordering. Electrical consequences (a rail collapsing, an input floating) belong to
`walk-fault-states`: when a finding ends in "and then the circuit does X", hand X there
and record the hand-off.

## 1. Start from what the checks decide

Run `.venv/bin/pcbkit check` and read `out/checks/results.json`. Take these as given:

- Polarity marks at wire pads: `test_wire_pads_have_polarity_marks` (`fab`, `WIRE_PADS`).
- Clearances and copper at the edge: `test_drc_clean_with_schematic_parity` (`kicad`).
- On an ESP32-S3 DevKit board (`esp32s3`): the current a GPIO sources and its absolute
  maximum in each `GPIO_SCENARIOS` row, which is how a signal pin that becomes a plug's
  ground return is judged once that case is a row.
- A project check reading `PIN_NETS` and `MATING` (proposed tables, a previous run of
  this review): for the plugs it lists, which net lands on which pin when reversed,
  offset or in the wrong port is decided. Spend the review on consequence and remedy.

## 2. Gather the inputs

Ask for what is missing; never guess a pinout.

- design.py for each connector's nets per pin; layout.py for where each sits and the
  outline; silk.py for `CONN_LABELS`; `pcbkit shots` for the pictures (its renders,
  `render_iso.png` and `render_top.png`, show the parts in 3D).
- The enclosure: a 3D model or STEP, and how boards stack.
- What plugs in at each connector: the cable or device, its voltage, and whether it is
  the user's own or third-party.
- Physical samples where fit is in doubt. A datasheet says what is meant to mate, not
  what can be forced: mark untested fits `test` and ask the user to try them.

## 3. Walk it

1. **Inventory.** One row per connector, button, switch, test point and port: reference,
   family and pitch, pin count, keying (polarised housing, shroud, latch, none), net and
   voltage per pin, what plugs in.
2. **Fit matrix.** Rows are every plug in the system, columns every socket on the board:
   `mates` (intended), `fits` (seats fully), `forced` (with force, offset or upside
   down), `no`, `test`. A plug with fewer pins in the same family sits in a wider shroud
   or along an unshrouded row: check both offsets.
3. **Walk each connector** through reversed, wrong port, offset by one, hot-plugged,
   left unplugged and shorted by a tool, pin by pin, with
   [references/misuse-walk.md](references/misuse-walk.md).
4. **Walk the assembly**, case closed and boards stacked: fingers and plug reach each
   connector, latches can be pressed, cables clear moving parts, labels on silkscreen
   and case agree, reset and programming are reachable, and something other than solder
   joints takes a yank.
5. **Remedy in this order.** Design it out (another family or pin count per function,
   keying, a pin order where reversal or offset is harmless), then protection parts
   (series resistor, fuse, diode, TVS), then labels. A label is the remedy only when the
   first two cost more than the harm.

A full walk of a generic board: [references/example.md](references/example.md). Read it
before a first review.

## Output

Use exactly these sections.

```text
### Connector inventory
| Ref | Family / pitch | Pins | Keying | Net and V per pin | What plugs in |

### Fit matrix
| Plug \ Socket | J1 | J2 | ... |     cells: mates / fits / forced / no / test
Hazards: one line per hazardous fits/forced cell - plug, socket, what lands where.

### Findings
| # | Connector | Misuse | Consequence | Suggested change (design-out / protection / label) |

### Reachability
- [ ] or [x] each connector mateable with case closed and boards stacked
- [ ] cables clear moving parts; bend radius met
- [ ] labels on silkscreen and case, matching
- [ ] reset, boot, test points, programming port reachable without disassembly
- [ ] yank load taken by something other than solder joints
Hand-offs: findings passed to walk-fault-states, one line each.

### Written to specs.py
```

Severity goes in the Consequence column in plain words (smoke, silent failure, safety
defeat, nuisance). Do not pad the findings with intended matings that work.

## 4. Write it down

- `PIN_NETS` (proposed): `{ref: {pad: net}}` for every connector, the port map.
- `MATING` (proposed): each plug's wires (`pins`, net or None per position), the sockets
  it `fits`, and whether it is keyed. With `PIN_NETS`, a check can then land every plug
  reversed, offset and in every port it fits. Hand both to `add-check`.
- `WIRE_PADS` for every bare pad that takes a wire.
- `GPIO_SCENARIOS` (ESP32-S3 boards): a row for each plug whose reversal puts current
  through a GPIO, so the series resistor is held by a check.
- silk.py: a `CONN_LABELS` entry for every connector, named by function.

## Lane and hand-offs

Unplugged and hot-plug consequences go to `walk-fault-states`; contact current and
ratings to `review-derating`; buying the right connector variant to `review-sourcing`.
A kind of finding seen on a second board becomes a check (`add-check`) and leaves this
skill.
