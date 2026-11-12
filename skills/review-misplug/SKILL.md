---
description: >-
  Review a pcbkit board and its enclosure for what hands, cables and tools do to it -
  every connector, button and user-reachable part checked for reversed, wrong-port,
  offset-by-one, hot-plugged, left-unplugged and tool-shorted mating, plus finger reach,
  cable routing, labels and pull-out loads - and write each plug and the sockets it
  fits into specs.py so a check lands every plug on the netlist. Run it after
  placement; load it before reviewing, planning or advising on connector choice,
  connector pinouts, port layout or enclosure cut-outs, even for one connector. Without it Claude
  checks only the intended mating, trusts family or pin count to stop a wrong-port mate,
  misses a narrower plug seated offset in a wider socket, works out pin landings by
  hand instead of by a check, and reaches for protection parts or labels before
  changing the family, keying or pin order.
---

# Mis-plug and physical usability review

What hands, cables and tools do to the board. Gate: after `pcbkit build`. Which net
lands on which pin is a netlist question for a check; whether a plug physically fits,
and what the landing does, is this review. When a finding ends in "and then the circuit
does X", hand X to `walk-fault-states` and record the hand-off.

## 1. Start from the checks that apply at this gate

A placed board exists, but of the built-in checks only the netlist ones bear on plugs
now: the `kicad` and `circuit` groups, and `esp32s3` on an ESP32-S3 DevKit board. Switch
on in `[checks] groups` of pcbkit.toml any group the first line says is off:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" kicad circuit
.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"
```

Add `or test_<topic>` for a previous run's mating check. `esp32s3` holds each GPIO
under the powered chip's 3.6 V in each `GPIO_SCENARIOS` row; it does not judge the
current through a pin that becomes a plug's ground return, nor a plug that drives an
unpowered board: those are step 4's. `test_wire_pads_have_polarity_marks` needs the
silkscreen `pcbkit route` adds, and DRC a routed board: a check whose inputs do not
exist yet is not a finding. A `-k` run is partial; the order gate runs all.

## 2. Gather the inputs and the fit

Ask for what is missing; never guess a pinout.

- design.py for the connectors; layout.py for where each sits; silk.py for
  `CONN_LABELS`; `pcbkit shots` (`render_iso.png`, `render_top.png` show the parts in
  3D); the enclosure (a 3D model or STEP, how the board mounts, what covers it).
- Every plug that will exist near the board: the cable or device, its wires, its
  voltage, and whether it is the user's own or third-party.
- **The fit matrix**, the judgement: rows are plugs, columns sockets. `mates`
  (intended), `fits` (seats fully), `forced` (with force, offset or upside down), `no`,
  `test`. A plug with fewer pins in the same family sits in a wider shroud or along an
  unshrouded row: check both offsets. A datasheet says what is meant to mate, not what
  can be forced: mark untested fits `test` and ask the user to try them.

## 3. Write the plugs down, then let a check land them

Proposed tables, which no built-in check reads yet; their shapes and the check's rule
are in "Proposed tables" in `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

- `PIN_NETS` for each socket a plug fits: the pad set the check lands plugs on.
- `MATING`: each plug meant for this board, the board net its wire at each position is
  meant to meet, every socket it seats in (`fits` or `forced` cells), `keyed`, `rows`.
  A cable meant for another device stays in the fit matrix: it has no intended landing.
- `NET_LIMITS` for each input a wire could overdrive, its absolute maximum, sourced.
- Hand them to `add-check`. Its project check lands each plug straight, reversed and
  offset, fails a source fight or an over-voltage, and records adjacent pads at
  different voltages. Its failures are the pin-by-pin hazards. It cannot see wires a
  plug joins inside itself (a switch contact across two wires): judge those.
- `WIRE_PADS` for every bare pad that takes a wire; `GPIO_SCENARIOS` (ESP32-S3) for a
  landing that puts a voltage on a GPIO of the powered board; in silk.py a
  `CONN_LABELS` entry for every connector, named by function.

## 4. Judge consequence and remedy

- Per failed landing and per `forced` or `test` cell: what the plugged device and the
  board do, with [references/misuse-walk.md](references/misuse-walk.md): smoke, a safety
  defeat, a silent failure, a nuisance. Hot-plug order and an input left unplugged
  depend on the circuit: hand them to `walk-fault-states`.
- **The assembly**, case closed: fingers and plug reach each connector, latches can be
  pressed, cables clear moving parts, labels on silkscreen and case agree, reset and
  programming are reachable, and something other than solder joints takes a yank.
- **Remedy in this order.** Design it out (another family or pin count per function,
  keying, a pin order where reversal or offset is harmless), then protection parts
  (series resistor, fuse, diode, TVS), then labels. A finished review of a generic
  board, for the shape of the fit matrix and the rows: [references/example.md](references/example.md).

## Lane and hand-offs

Unplugged and hot-plug consequences go to `walk-fault-states`; contact current and
ratings to `review-derating`; buying the right connector variant to `review-sourcing`.

## Output

Use exactly these sections.

```text
### Connector inventory
| Ref | Family / pitch | Pins | Keying | Net and V per pin | What plugs in |

### Fit matrix
| Plug \ Socket | J1 | J2 | ... |     cells: mates / fits / forced / no / test
Hazards: one line per failed landing the check reported - plug, socket, what lands where.

### Findings
| # | Connector | Misuse | Consequence | Suggested change (design-out / protection / label) |

### Reachability
- [ ] or [x] each connector mateable with the case closed
- [ ] cables clear moving parts; bend radius met
- [ ] labels on silkscreen and case, matching
- [ ] reset, boot, test points, programming port reachable without disassembly
- [ ] yank load taken by something other than solder joints
Hand-offs: findings passed to walk-fault-states, one line each.

### Written to specs.py
```

Severity goes in the Consequence column in plain words; do not pad with matings that work.
