---
description: >-
  Write the step-by-step bring-up plan for the first assembled pcbkit board and use the
  writing as a design review - every probe point the plan needs and the layout lacks,
  and every section that cannot be powered alone, is a finding - and record the test
  points, isolation links and expected readings as check tables. Load it before
  writing or reviewing a bring-up, first-power or bench plan, when asked whether a
  board can be tested section by section or has the test points it needs, and when
  boards arrive and need a bench checklist, even for a small board. Without it Claude
  starts on a battery instead of a current-limited supply, brings every rail up at
  once, gives no expected idle current or stop condition per step, works out bus
  addresses and expected readings by hand that the checks record, and assumes test
  points and isolation jumpers the layout does not have.
---

# Bring-up plan

Gate: after `pcbkit finalize`, before an order (`board-workflow` runs it). The plan
is the bench checklist when boards arrive. A step that cannot be carried out is a design
defect you can still fix: no test point, no link to isolate a section, no way to power
a section alone.

## Safety rules (never relax)

- First power is always a current-limited bench supply, never a battery. The product's
  own supply (an adapter, a battery) appears only after every bench step has passed.
- Ramp the supply up from 0 V while watching the current.
- Every step states a current limit with its reason and a stop condition.
- One section live at a time. Loads come last, smallest first.
- Protection is tested on the bench supply, never by misconnecting a battery.

## 1. Start from what the checks decide

Everything exists at this gate, so every group applies; the readings below need
`circuit` on. Switch on in `[checks] groups` of pcbkit.toml any group the first line
says is off, then run the checks in full and read `out/checks/results.json`:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" kicad circuit
.venv/bin/pcbkit check
```

- What the design answers on each bus: the `i2c` table that
  `test_i2c_addresses_unique_per_bus` records. Never work an address out again.
- Expected rail voltages: `SUPPLY_PINS` and `test_supply_pins_in_range`.
- Default-off states: a project check reading `FAULT_STATES`, if `walk-fault-states`
  wrote one. The plan tests those states on the bench; it does not re-derive them.
- A check that failed or was skipped is a step that cannot pass on the bench: list it
  in Part 1 and stop there until it is fixed.

## 2. Settle the power tree and the bench

From design.py and the schematic PDF in `out/docs/`: the power tree (sources, protection,
regulators, rails, what feeds what) and the enable chain; from the user, the bench
(supply, scope) and what is mated during testing. Stages: the lowest-energy section that
runs alone first, then the input stage alone, each rail in dependency order, then loads.
A rail that cannot come up without another one coming up too is a finding.

## 3. Write the rows, then let the checks decide

The tables are proposed: no built-in check reads them yet. Their shapes are in
"Proposed tables" in `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

- `TEST_POINTS`: every net a step probes, with the ground distance a scope spring tip
  needs. `PROBE_KEEPAWAY`: the boxes mated plugs and fitted modules cover.
- `ENABLE_LINKS`: each enable net several regulators share, with each regulator's
  enable pad, so each can be isolated.
- `I2C_EXPECTED`: the exact scan per bus, from the user or the spec, never from the
  recorded `i2c` table (that would compare the design with itself).
- `BRINGUP_STAGES`: each stage's scenario, supply net and current limit. Its readings
  check records each stage's input current and each test point's resistance to ground:
  the expected readings, never arithmetic in the conversation. The model sees only
  resistors, diodes and FETs, so a bench reading can be lower.
- `SUPPLY_PINS` for any rail whose window the plan relies on and no row holds;
  design.py `dnp=True` for parts left off for first power; a layout.py `SHOTS` region
  per stage's probe points.

Hand each table to `add-check`, then run `pcbkit check` in full again. A failed row is
a Part 1 finding.

## 4. Write the plan

Steps by stage from [references/bench-method.md](references/bench-method.md): inspection
and resistance first, then each stage with its limit (the `BRINGUP_STAGES` row), the
firmware ladder one bus at a time, each protection the board has exercised on purpose
on the bench supply, scope steps marked "(scope)". Each expectation is a recorded number
or a `SUPPLY_PINS` window, named. A plan for a generic board, for the shape of both
parts: [references/example.md](references/example.md).

## Lane and hand-offs

Default states go to `walk-fault-states`, ratings to `review-derating`, return paths to
`review-current-paths`, plugs to `review-misplug`, the spec to `review-spec-drift`.

## Output - fixed format

Write it to `bringup.md` in the project folder (never `out/` or `fab/`, which pcbkit
rewrites), and show Part 1 in the reply.

**Part 1. Design findings**

| # | Kind | Where | Why the plan needs it | Suggested change |
|---|---|---|---|---|

Kind: missing test point, missing isolation, unreachable probe point, cannot power
alone, unsafe default state, failing check. Then "Hand-offs": the review and the
question, one line each.

**Part 2. Bring-up plan**, after an "Initial state" line (links open or fitted,
switches, connectors, what is unpopulated):

| Step | Action | Probe point | Expected | Limit | Stop if |
|---|---|---|---|---|---|

Steps numbered by stage (0.1, 0.2, 1.1 ...); stage 0 is always inspection and
resistance with no power. A probe point names a test point or net on the board; one
that does not exist yet gets the name it should have and a Part 1 row. Limit is the
supply voltage and current limit in force. Stop if is a concrete reading ("supply in
constant current over 1 s", "address 0x48 missing"), never "looks wrong". Do not change
the columns: the user records the measured value per step on the bench.
