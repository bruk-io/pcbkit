---
description: >-
  Review a pcbkit board and its enclosure for what hands, cables and tools do to it -
  every connector, button and user-reachable part checked for reversed, wrong-port,
  offset-by-one, hot-plugged, left-unplugged and tool-shorted mating, plus finger reach,
  cable routing, labels and pull-out loads - and write each plug and the sockets it
  fits into specs.py so a check lands every plug on the netlist. Run it after
  placement; load it before reviewing, planning or advising on connector choice,
  pinouts, port layout or enclosure cut-outs, even for one connector. Without it Claude
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

A placed board exists, but of the built-in checks only the netlist ones apply to plugs
now: the `circuit` group, `test_erc_clean`, and on an ESP32-S3 DevKit board the
`esp32s3` group (a GPIO's source current and absolute maximum in each `GPIO_SCENARIOS`
row, which is how a signal pin that becomes a plug's ground return is judged). Run them,
adding `or test_<topic>` for a previous run's mating check:

```
.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"
```

`test_wire_pads_have_polarity_marks` (`WIRE_PADS`) needs the silkscreen `pcbkit route`
adds, and DRC a routed board: a check whose inputs do not exist yet is not a finding. A
`-k` run leaves partial results; the order gate runs all. A gate option (WP17, --gate
place) will replace this selection.

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

- `MATING`: each plug's wires (`pins`, net or None per position), every socket it
  `fits` or can be `forced` into, and whether it is `keyed`. A proposed table no
  built-in check reads yet (shape in "Proposed tables",
  `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`).
- Hand it to `add-check`. Its project check reads each socket's pad nets from the
  netlist, lands every plug as intended, reversed (unless keyed) and offset by one in
  each socket it fits, fails a supply on ground or a signal, a higher rail on a
  lower-rated pin, or a loop closed by the wrong device, and lists adjacent pads at
  different voltages (tool shorts). Run it; its failures are the pin-by-pin hazards.

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
  (series resistor, fuse, diode, TVS), then labels.

For the shape of the fit matrix and of `MATING`, when unclear: a finished review of a
generic board, [references/example.md](references/example.md).

## 5. Write the rest down

- `WIRE_PADS` for every bare pad that takes a wire; `GPIO_SCENARIOS` (ESP32-S3): a row
  per plug whose reversal puts current through a GPIO, so a check holds the resistor.
- silk.py: a `CONN_LABELS` entry for every connector, named by function.

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
