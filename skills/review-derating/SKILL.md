---
description: >-
  Review a pcbkit board's parts for ones whose nominal value passes and still fails
  under the board's worst case - capacitance lost to DC bias, voltage margin against
  transients, current per connector contact, inductor saturation, fuse hold and inrush
  I2t, peak-load budgets, dropout, set-point tolerance - and write the envelope and limits
  into specs.py so the circuit checks hold them. Load it after the schematic, and
  whenever asked whether a part, rail, connector or fuse is rated enough, even one part.
  Without it Claude re-derives what the checks measure, checks nominals, budgets
  running current instead of peak, rates connectors per connector instead of per
  contact, and invents derated figures instead of naming the curve to look up.
---

# Derating review

What a part is sold as against what it delivers under this board's worst case. Gate:
after `pcbkit sch` is clean; `board-workflow` runs it again before every order.

## Standing rules

- **Never invent a derated figure.** If the real value needs a maker's curve or tool,
  the cell names what to look up (part number, tool, operating point), the verdict is
  `LOOKUP`, and the `parts-researcher` agent looks it up.
- **Never compute a stress or a margin in the conversation**: both come from a check's
  record or from `stress.py` (step 1).
- **Worst case, not nominal**: highest real voltage with transients, peak not average,
  start-up and fault current not running, hottest ambient inside the enclosure, lowest
  real supply.
- **Per contact, not per connector**: the net's current over the contacts that carry
  it, after the maker's derating for contact count and temperature.
- **Every load figure has an owner**: `datasheet`, `measured` or `assumed`. An assumed
  figure at the limit is a finding, and its fix is a measurement.

## 1. Start from the checks that apply at this gate

At the schematic gate only the netlist exists. The script names any group pcbkit.toml
leaves off (switch it on in `[checks] groups`) and prints the gate's run with its
caveats; run what it prints, adding `or test_<topic>` per project check that reads only
the netlist, then `stress.py`:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" --gate sch
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/review-derating/scripts/stress.py"
```

The built-in checks record only the four most stressed parts
and judge supply pins at `SUPPLY_SCENARIO` alone. `stress.py` solves every
`STRESS_SCENARIOS` row and prints, as JSON, each capacitor, resistor and FET or diode
with its worst stress, the scenario, its rating on file, margin and verdict against the
house target (a `SEMI_VRATED` row's own `derate` for a semiconductor), each `SUPPLY_PINS` row at every scenario and each `SUPPLY_LIMITS` row's
margin. Quote it; never work a number out again.

## 2. Settle the envelope

Each a stated assumption with its source; ask the user, and mark what is missing
`UNSTATED` rather than guess. Supply: lowest real voltage under load, highest, and
transients (hot-plug ringing, a motor braking, an inductive kick). Ambient: inside the
enclosure. Loads: running and peak current (start-up, stall or fault), how many peak at
once and what decides that, and the duty.

## 3. Write the envelope into specs.py, then run

- `STRESS_SCENARIOS`: one row per envelope corner, the transient included (extend
  `scenario` in circuits.py for keywords it lacks). `CAP_VRATED` for every capacitor
  part number, `RESISTOR_POWER_W` for every resistor off the default, each sourced.
- `SUPPLY_LIMITS`, one row per arithmetic question: a budget behind a fuse or
  connector, a set-point's worst case, inrush I2t against the fuse's derated melting
  I2t, inductor saturation against the current limit, effective capacitance against the
  regulator's minimum, dropout at the lowest input. A value that comes from the netlist
  or a solve is a function row that reads or solves it, never a number copied in.
- `CONTACT_RATINGS`, `SEMI_VRATED`: proposed tables no built-in check reads yet (shapes
  in "Proposed tables", `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`): `add-check`.
- `SUPPLY_PINS` for the supply pins the rows touch; `COPPER_BUDGETS` `i_cont` and
  `i_peak` from the envelope, for the route gate.

Run the selection and `stress.py` again: every failing row is a finding code decided.

## 4. Judge what the checks cannot

Whether each scenario, rating and budget is the true worst case, and what to look up,
part class by part class: [references/part-classes.md](references/part-classes.md). For
the shape of a finding or a `SUPPLY_LIMITS` function row, see the worked review of a
generic board, [references/example.md](references/example.md).

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

Stress, margin, `OK` and `FAIL` come from `stress.py` or a check, never worked out here.
The review adds `MARGINAL` (a pass resting on an assumed figure, or under a class target
the house 80 % misses), `LOOKUP` (the cell names the curve or tool) and `UNSTATED`. A
source cell names the check or the `stress.py` entry.
