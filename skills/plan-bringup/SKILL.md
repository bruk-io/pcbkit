---
description: >-
  Write the step-by-step bring-up plan for the first assembled pcbkit board and use the
  writing as a design review - every probe point the plan needs and the layout lacks,
  and every section that cannot be powered alone, is a finding to fix before ordering -
  and record the test points, isolation links and expected readings as check tables.
  Load it before ordering boards, before writing or reviewing a bring-up or first-power
  plan, and when boards arrive and need a bench checklist, even for a small board.
  Without it Claude starts on a battery instead of a current-limited supply, brings
  every rail up at once, gives no expected idle current or stop condition per step,
  works out bus addresses the checks already record, and assumes test points and
  isolation jumpers the layout does not have.
---

# Bring-up plan

Gate: before ordering, after `pcbkit finalize` and a full `pcbkit check`. The plan
written here is the bench checklist when boards arrive, so write it to be carried out,
not read. A step that cannot be carried out is a design defect you can still fix: no
test point to probe, no link to isolate a section, no way to power a section alone.

## Safety rules (never relax)

- First power is always a current-limited bench supply, never a battery. A battery or
  pack appears only after every bench step has passed.
- Ramp the supply up from 0 V while watching the current.
- Every step states a current limit with its reason and a stop condition.
- One section live at a time. Loads come last, smallest first.
- Protection is tested on the bench supply, never by misconnecting a battery.

## 1. Start from what the checks decide

Read `out/checks/results.json` from a full run (`selection.complete` true):

- The expected bus scan: the `i2c` table `test_i2c_addresses_unique_per_bus` records
  per bus. All-call and general-call addresses count if `circuits.i2c_devices` lists
  them. Copy it; never work an address out again.
- Expected rail voltages: `SUPPLY_PINS` and `test_supply_pins_in_range`.
- Default-off states: a project check reading `FAULT_STATES`, if `walk-fault-states`
  wrote one. The plan tests those states on the bench; it does not re-derive them.
- A check that failed or was skipped is a step that cannot pass on the bench: list it
  under Part 1 and stop there until it is fixed.

## 2. Gather the inputs

design.py and the schematic PDF in `out/docs/` for the power tree (every source,
protection part, regulator and rail, and what feeds what) and the enable chain (what
holds each regulator off, what releases it, its state with the controller unpowered,
in reset or blank); layout.py and `pcbkit shots` for where each probe point is and what
covers it when assembled; the assembly drawing in `out/docs/`. From the user: the bench
(supply voltage and current, a scope or not) and what is mated during testing.

## 3. Write it

1. **Order from the power tree and the enable chain.** The lowest-energy section that
   runs alone goes first (usually the controller from USB or a logic rail), then the
   input stage alone, then each rail in dependency order, then loads. A rail that cannot
   come up without another one also coming up is a finding.
2. **For every step: what does the probe touch, and can it reach it assembled**
   (connectors mated, modules fitted)? A probe point that does not exist, is under a
   part or is blocked by a mated plug is a finding. Each needs a ground pad close
   enough for a scope spring tip.
3. **For every section: how is it isolated** so a fault in it does not take out the
   rest? Zero-ohm links, solder jumpers, parts left `dnp=True`, enables held off by
   default. A section with none is a finding.
4. **State expectations as numbers you derived**: resistance order of magnitude, idle
   current from quiescent currents, rail voltage and tolerance. Show the arithmetic in
   the cell, briefly.
5. **Firmware ladder**: programming, then one bus or peripheral at a time.
6. **Exercise every protection** on purpose: stop input, reverse polarity at low voltage
   and current, over-current through a monitor's alert, not a dead short.
7. **Mark scope steps** with "(scope)"; everything else needs only a meter and the
   supply readout.

Stages, resistance expectations, current limits and protection tests:
[references/bench-method.md](references/bench-method.md). A plan for a generic board:
[references/example.md](references/example.md); read it before a first plan.

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

## 4. Write it down

- `TEST_POINTS` and `PROBE_KEEPAWAY` (proposed): the nets that need a test point, the
  ground distance, and the boxes mated plugs and modules cover. `ENABLE_LINKS`
  (proposed): which regulators on a shared enable need their own link. `I2C_EXPECTED`
  (proposed): the exact scan per bus. Hand each to `add-check`.
- `SUPPLY_PINS` for any rail whose expected window the plan relied on and no row held.
- design.py: `dnp=True` for parts left off for first power; layout.py: a `SHOTS`
  region per stage's probe points.

## Lane and hand-offs

Default states go to `walk-fault-states`, ratings to `review-derating`, return paths to
`review-current-paths`, plugs to `review-misplug`, a spec question to
`review-spec-drift`. A finding seen on a second board becomes a check (`add-check`).
