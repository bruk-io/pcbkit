---
description: >-
  Review a pcbkit board's parts for ones whose nominal value passes and still fails
  under the board's worst case - ceramic capacitance lost to DC bias and temperature,
  voltage margin against transients, connector current per contact, inductor
  saturation at peak current, fuse hold, trip and inrush I2t, stall-current budgets,
  supply range and dropout, set-point tolerance stacks - and write the envelope and
  limits into specs.py so the circuit checks hold them. Load it after the schematic and
  before layout, again before ordering if parts changed, and whenever asked whether a
  part, rail, connector or fuse is rated enough, even one part. Without it Claude
  re-derives what the capacitor and resistor checks already measure, checks nominals,
  budgets running current instead of stall, rates connectors per connector instead of
  per contact, and invents derated figures instead of naming the curve to look up.
---

# Derating review

Find the gap between what a part is sold as and what it delivers under this board's
worst case. This review owns the numbers: stress against real capability. Gate: after
the schematic (`pcbkit sch` clean), and before ordering if parts changed.

## Standing rules

- **Never invent a derated figure.** If the real value needs a maker's curve or tool,
  the cell names exactly what to look up (part number, tool or figure, operating point)
  and the verdict is `LOOKUP`. Dispatch the `parts-researcher` agent for each.
- **Envelope first.** No part is judged until the worst-case envelope is written down.
- **Worst case, not nominal**: highest real voltage with transients, peak not average,
  stall not running, hottest ambient inside the enclosure, lowest real supply.
- **Per contact, not per connector**: the net's current over the contacts that carry
  it, after the maker's derating for contact count and temperature.
- **Every load figure has an owner**: `datasheet`, `measured` or `assumed`. An assumed
  figure at the limit is a finding, and its fix is a measurement.

## 1. Start from what the checks decide

Run `.venv/bin/pcbkit check` and read `out/checks/results.json`. The `circuit` group
already measures, at every `STRESS_SCENARIOS` operating point:
- `test_capacitor_voltage_derated`: each capacitor under 80 % of its `CAP_VRATED`.
- `test_resistor_power_derated`: each resistor under half its `RESISTOR_POWER_W`.
- `test_led_currents`, `test_supply_pins_in_range` (`SUPPLY_PINS`, `SUPPLY_LIMITS`).

The `copper` group's `test_copper_segment` judges copper heating at `COPPER_BUDGETS`
`i_cont` and drop at `i_peak`. Read their recorded numbers for the stress and margin
columns; do not recompute them. What these checks cannot see is whether the scenario,
the rating and the budget are the true worst case: that is this review.

## 2. Write the envelope

As stated assumptions, each with its source. Ask the user for any you lack; mark it
`UNSTATED` rather than guess.

- Supply: lowest real voltage under load (cut-off plus sag), highest real voltage, and
  transients (hot-plug ringing into ceramic input capacitance, a motor braking, an
  inductive kick from a relay or a long lead).
- Ambient: inside the enclosure, not the room.
- Loads: running and stall current per load and their source; how many can peak at
  once, and what decides that (firmware limiting, the mechanics, nothing). Duty:
  continuous, intermittent or pulse, and for how long.

## 3. Go part class by part class

Ceramic capacitors, other capacitors, FETs and diodes, connectors and wire, inductors,
resistors and shunts, fuses, set-point dividers, then the supply edges: what to check
and look up in each is [references/part-classes.md](references/part-classes.md). Read
[references/example.md](references/example.md), a generic board, before a first review.

## Output

Exactly this shape:

```
## Envelope
- Supply: <min>V to <max>V, transients <what and how high> (source)
- Ambient: <min> to <max> C (source)
- Loads: <load>: running <A>, stall <A> (datasheet | measured | assumed); simultaneous peak: <n of m> (source)
- UNSTATED: <items still missing>

## Derating table
| Part / net | Nominal | Worst-case stress | Real capability (source) | Margin | Verdict |
|---|---|---|---|---|---|

## Findings
1. <part / net> - <risk in one line>. Fix: <specific change or measurement>.

## Written to specs.py
- <table>: <rows added or changed>
```

Margin is (capability - stress) / capability. Verdicts: `OK` (meets the target in
part-classes.md), `MARGINAL` (positive margin under the target, or resting on an
assumed figure), `FAIL` (stress over capability), `LOOKUP` (needs a curve or tool; the
cell names it), `UNSTATED` (the envelope does not say enough). A row a check measured
says so in its source cell (`test_capacitor_voltage_derated`).

## 4. Write it down

So the next run is a check, not this review:

- `STRESS_SCENARIOS`: one row per envelope corner, the transient included (the highest
  input with ringing, the lowest input at stall). The capacitor and resistor checks run
  at each; extend `scenario` in circuits.py for keywords it lacks.
- `CAP_VRATED` for every capacitor part number, `RESISTOR_POWER_W` for every resistor
  that is not the default, each with its datasheet source.
- `SUPPLY_LIMITS`, one row per arithmetic finding: the load budget behind a fuse or
  connector, the divider's worst-case output against the load's maximum (a function of
  the netlist, so a changed resistor is seen), inrush I2t against the fuse's derated
  melting I2t, inductor saturation against the regulator's current limit, effective
  capacitance from the maker's tool against the regulator's minimum, the lowest input
  against dropout. `SUPPLY_PINS` for the supply pins those rows touch.
- `COPPER_BUDGETS`: `i_cont` and `i_peak` from the envelope, not from guesses.
- `CONTACT_RATINGS` and `SEMI_VRATED` (proposed; no built-in check reads them yet):
  per-contact connector ratings, and FET and diode voltage ratings. Hand each to
  `add-check` for a project check.

## Lane and hand-offs

Chip behaviour (sequencing, enable logic) is `walk-fault-states`, copper and return
paths `review-current-paths`, a plug in the wrong place `review-misplug`. A kind of
finding seen on a second board becomes a check (`add-check`) and leaves this skill.
