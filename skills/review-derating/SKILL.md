---
description: >-
  Review a pcbkit board's parts for ones whose nominal value passes and still fails
  under the board's worst case - capacitance lost to DC bias, voltage margin against
  transients, current per connector contact, inductor saturation, fuse hold and inrush
  I2t, stall budgets, dropout, set-point tolerance - and write the envelope and limits
  into specs.py so the circuit checks hold them. Load it after the schematic, and
  whenever asked whether a part, rail, connector or fuse is rated enough, even one part.
  Without it Claude re-derives what the checks measure, checks nominals, budgets
  running current instead of stall, rates connectors per connector instead of per
  contact, and invents derated figures instead of naming the curve to look up.
---

# Derating review

What a part is sold as against what it delivers under this board's worst case. Gate:
after `pcbkit sch` is clean; `board-workflow` reruns it before an order if parts changed.

## Standing rules

- **Never invent a derated figure.** If the real value needs a maker's curve or tool,
  the cell names what to look up (part number, tool, operating point), the verdict is
  `LOOKUP`, and the `parts-researcher` agent looks it up.
- **Never compute a stress in the conversation**: it comes from a check's record or a
  solve of `circuits.scenario` (step 1).
- **Worst case, not nominal**: highest real voltage with transients, peak not average,
  stall not running, hottest ambient inside the enclosure, lowest real supply.
- **Per contact, not per connector**: the net's current over the contacts that carry
  it, after the maker's derating for contact count and temperature.
- **Every load figure has an owner**: `datasheet`, `measured` or `assumed`. An assumed
  figure at the limit is a finding, and its fix is a measurement.

## 1. Start from the checks that apply at this gate

At the schematic gate only the netlist exists: the `circuit` group, `test_erc_clean`
and `esp32s3` on a DevKit board apply. Run exactly those, adding `or test_<topic>` for
a project check that reads only the netlist and specs.py:

```
.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"
```

`outputs`, DRC and `copper` (`test_copper_segment` too) need a routed board or fab
files: a check whose inputs do not exist yet is not a finding. A `-k` run leaves partial
results; the order gate runs all. A gate option (WP17, --gate sch) will replace this.

What results.json holds: `test_capacitor_voltage_derated` and
`test_resistor_power_derated` solve every `STRESS_SCENARIOS` row but record only the
four most stressed parts (a failure lists every part over its limit);
`test_led_currents` runs at `LED_SCENARIO` only; `test_supply_pins_in_range` runs at
`SUPPLY_SCENARIO` only and records nothing, so supply-pin windows are not checked at
the envelope corners. Until WP17 records every part at every row, solve the rest: in
`.venv/bin/python`, `pcbkit.check.netlist.Netlist.from_file("kicad/<stem>.net")`, then
`circuits.scenario(nl, **row).solve()` per `STRESS_SCENARIOS` row; print each part's
voltage, `resistor_power()` and each `SUPPLY_PINS` row's voltage, and quote the output.

## 2. Settle the envelope

Each a stated assumption with its source; ask the user, and mark what is missing
`UNSTATED` rather than guess. Supply: lowest real voltage under load, highest, and
transients (hot-plug ringing, a motor braking, an inductive kick). Ambient: inside the
enclosure. Loads: running and peak or stall current, how many peak at once and what
decides that, and the duty.

## 3. Write the envelope into specs.py, then run

- `STRESS_SCENARIOS`: one row per envelope corner, the transient included (extend
  `scenario` in circuits.py for keywords it lacks). `CAP_VRATED` for every capacitor
  part number, `RESISTOR_POWER_W` for every resistor off the default, each sourced.
- `SUPPLY_LIMITS`, one row per arithmetic question: a budget behind a fuse or
  connector, a set-point's worst case (a function of the netlist), inrush I2t against
  the fuse's derated melting I2t, inductor saturation against the current limit,
  effective capacitance against the regulator's minimum, dropout at the lowest input.
- `CONTACT_RATINGS`, `SEMI_VRATED`: proposed tables no built-in check reads yet (shapes
  in "Proposed tables", `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`): `add-check`.

Run the selection again: every failing row is a finding the checks decided.
## 4. Judge what the checks cannot

Whether each scenario, rating and budget is the true worst case, and what to look up,
part class by part class: [references/part-classes.md](references/part-classes.md). For
the shape of a finding or a `SUPPLY_LIMITS` function row, see the worked review of a
generic board, [references/example.md](references/example.md).

## 5. Write the rest down

`SUPPLY_PINS` for the supply pins the rows touch; `COPPER_BUDGETS` `i_cont` and `i_peak`
from the envelope, for the route gate.

## Lane and hand-offs

Chip behaviour is `walk-fault-states`, copper and return paths `review-current-paths`,
a plug in the wrong place `review-misplug`.

## Output

Exactly this shape:

```
## Envelope
- Supply: <min>V to <max>V, transients <what and how high> (source)
- Ambient: <min> to <max> C (source)
- Loads: <load>: running <A>, peak <A> (datasheet | measured | assumed); simultaneous peak: <n of m> (source)
- UNSTATED: <items still missing>

## Derating table
| Part / net | Nominal | Worst-case stress | Real capability (source) | Margin | Verdict |
|---|---|---|---|---|---|

## Findings
1. <part / net> - <risk in one line>. Fix: <specific change or measurement>.

## Written to specs.py
- <table>: <rows added or changed>
```

Margin is (capability - stress) / capability. Verdicts: `OK` (meets the part-classes.md
target), `MARGINAL` (under it, or resting on an assumed figure), `FAIL`, `LOOKUP` (the
cell names the curve or tool), `UNSTATED`. A source cell names the check or the solve.
