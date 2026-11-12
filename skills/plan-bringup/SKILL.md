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

Gate: after `pcbkit finalize`, before an order (`board-workflow` sequences it). The plan
is the bench checklist when boards arrive. A step that cannot be carried out is a design
defect you can still fix: no test point, no link to isolate a section, no way to power
a section alone.

## Safety rules (never relax)

- First power is always a current-limited bench supply, never a battery. A battery or
  pack appears only after every bench step has passed.
- Ramp the supply up from 0 V while watching the current.
- Every step states a current limit with its reason and a stop condition.
- One section live at a time. Loads come last, smallest first.
- Protection is tested on the bench supply, never by misconnecting a battery.

## 1. Start from what the checks decide

Everything exists at this gate, so every group applies: run `.venv/bin/pcbkit check`
with no `-k` and read `out/checks/results.json` (`selection.complete` true).

- The expected bus scan: the `i2c` table `test_i2c_addresses_unique_per_bus` records
  per bus. Copy it; never work an address out again.
- Expected rail voltages: `SUPPLY_PINS` and `test_supply_pins_in_range`.
- Default-off states: a project check reading `FAULT_STATES`, if `walk-fault-states`
  wrote one. The plan tests those states on the bench; it does not re-derive them.
- A check that failed or was skipped is a step that cannot pass on the bench: list it
  in Part 1 and stop there until it is fixed.

## 2. Settle the power tree and the bench

From design.py and the schematic PDF in `out/docs/`: the power tree (every source,
protection part, regulator and rail, and what feeds what) and the enable chain. From the
user: the bench (supply voltage and current, a scope or not) and what is mated during
testing. Order the stages: the lowest-energy section that runs alone first (usually the
controller from USB or a logic rail), then the input stage alone, then each rail in
dependency order, then loads. A rail that cannot come up without another one also coming
up is a finding.

## 3. Write the rows, then let the checks decide

The tables are proposed: no built-in check reads them yet. Their shapes are in
"Proposed tables" in `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

- `TEST_POINTS`: every net a step probes, with the ground distance a scope spring tip
  needs. `PROBE_KEEPAWAY`: the boxes mated plugs and fitted modules cover.
- `ENABLE_LINKS`: each enable net several regulators share, so each can be isolated.
- `I2C_EXPECTED`: the exact scan per bus, from the recorded `i2c` table.
- Expected readings, never arithmetic in the conversation: `add-check` writes a project
  check that solves with `pcbkit.check.dc` and records each `TEST_POINTS` net's
  resistance to ground, rails unpowered (a test current from `Circuit.inject`), and each
  stage's input current from a `circuits.scenario` with datasheet quiescent currents as
  loads. The model sees only resistors, diodes and FETs: a bench reading can be lower.

Hand each table to `add-check`, then run `pcbkit check` in full again. A failed
`TEST_POINTS`, `PROBE_KEEPAWAY` or `ENABLE_LINKS` row is a Part 1 finding.

## 4. Write the plan

Steps by stage from [references/bench-method.md](references/bench-method.md): inspection
and resistance first, then each stage with its limit (2 to 3 times the recorded idle
current), the firmware ladder one bus at a time, every protection exercised on purpose
(stop input, reverse polarity at low voltage and current, over-current through a
monitor's alert, not a dead short), scope steps marked "(scope)". Each expectation is a
recorded number or a `SUPPLY_PINS` window, named. A plan for a generic board, for the
shape of both parts: [references/example.md](references/example.md).

## 5. Write the rest down

- `SUPPLY_PINS` for any rail whose expected window the plan relied on and no row held.
- design.py: `dnp=True` for parts left off for first power; layout.py: a `SHOTS`
  region per stage's probe points.

## Lane and hand-offs

Default states go to `walk-fault-states`, ratings to `review-derating`, return paths to
`review-current-paths`, plugs to `review-misplug`, a spec question to
`review-spec-drift`.

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
