---
description: >-
  Walk a pcbkit board's circuit through every abnormal power and control condition
  (logic rail missing, controller in reset or its bootloader, firmware hung, USB-only
  power, brownout, stop input open, back-feed from another powered device, supply pulled
  under load, hot-plug) and state what every actuator output and rail enable does in
  each, as a fixed state table plus findings, then write the intended states into
  specs.py so a check holds them. Load it after the schematic and before layout on any
  board that drives motors, heaters, relays or other actuators, and before answering
  "is this fail-safe", "what happens if the controller dies" or a power-sequencing
  question about one, even a small one. Without it Claude assumes pins idle low and
  regulators idle off; real defaults come from datasheets (an enable that floats on, a
  pull to a rail that is absent, outputs live during boot, signal pins back-powering an
  unpowered board), and an output whose state cannot be traced is itself a finding.
---

# Walking the fault states

A board that drives actuators must be safe when its controller is absent, confused or
half-powered. This review states, for each abnormal condition, what each output and
each rail enable does, traced from datasheet defaults and never assumed. Gate: after
the schematic is ERC-clean (`pcbkit sch`), before layout.

## 1. Start from what the checks decide

Run `.venv/bin/pcbkit check` (no `-k`: a selected run replaces the results) and read
`out/checks/results.json`. Take these as given and do not redo them:

- Floating and unconnected pins: `test_erc_clean` (`kicad`). An open pin with a
  no-connect flag in design.py was left open on purpose.
- Supply pins inside their range in the normal case: `test_supply_pins_in_range`
  (`circuit`, `SUPPLY_SCENARIO` and `SUPPLY_PINS`).
- On an ESP32-S3 DevKit board (`esp32s3`): strapping pins at reset
  (`test_strapping_pins_at_reset`), input-only pins with a defined level, and every GPIO
  under its absolute maximum in each `GPIO_SCENARIOS` row, which is how injected current
  from another powered device is judged.
- Any project check reading `FAULT_STATES` (a previous run of this review): its
  conditions are decided. Walk only the conditions and outputs it does not list.

A check that failed is a finding to report, not one to re-derive.

## 2. Gather the inputs

- `design.py` for every part and net, `circuits.py` for the `scenario` the checks
  solve, `specs.py` for what is already written down.
- The datasheet of every part with a pin on an output or enable path. Run
  `pcbkit datasheet find` on their part numbers, and dispatch the `parts-researcher`
  agent for each pin fact: internal pull direction and strength, enable thresholds,
  UVLO, power-good, the pin's state in reset and in the bootloader. Never trace from
  memory of a part.
- Intent per output: what it must do when the board is not in control. If no one has
  said, assume off for anything that moves or heats, and say so at the top.

## 3. Walk it

1. **Inventory.** Every actuator-facing output (driver outputs, PWM lines, gate drives,
   relay coils, motor enables) and every rail enable (regulator EN, load switch ON,
   driver output-enable, reset lines on driver chips), with the signal that controls
   each. These are the columns.
2. **Conditions.** Walk every row of
   [references/conditions.md](references/conditions.md), then ask the user for this
   board's own (a charger, a debug probe that powers the board, a second supply).
3. **Trace from the supply forward.** For each cell, start at the rails present in that
   condition and follow the path to the output. At every pin use what the datasheet
   says it does floating, unpowered or in reset, and quote that fact in the footnote.
4. **Ask who holds it.** An off that only firmware maintains is not off when firmware
   crashes, resets or sits in the bootloader. Mark which cells hold in hardware.
5. **Unknown is a finding.** If the datasheet is silent or the trace forks on something
   you cannot settle, the cell is `undefined`.

Recurring fixes: [references/patterns.md](references/patterns.md). A full walk of a
generic board: [references/example.md](references/example.md), read before a first walk.

## Output

Always this shape, nothing before it but one line naming the files and datasheets read.

**State table**: conditions as rows, outputs and rail enables as columns. Each cell is
exactly one of `off` (de-energised, held there by hardware), `on` (energised or able to
energise the actuator), `undefined` (floating, indeterminate or not traceable) or
`depends` (decided by something off the board or by timing; the footnote says what).
Footnote any cell whose reason is not obvious, with the datasheet fact it rests on.

**Findings**, numbered. Every `undefined` cell, and every `on` or `depends` cell where
intent says off. Each gives the cell (condition and output); what happens and why,
citing the datasheet; the consequence for the mechanism; a fix specific to this
design.py (pull direction and the rail it hangs from, a hold-down, the stop loop in the
enable chain, OR-ing, series resistance); and whether the fix takes firmware out of the
safety path.

**Measured by a check**: one line per failed or skipped check from step 1 that bears on
this walk. **Written to specs.py**: the rows you added (step 4). **Out of scope**: one
line each for what belongs to another review.

## 4. Write the intent down

Once the user agrees the intended state of each cell, record it so the next run is a
check:

- `FAULT_STATES` (proposed, no built-in check reads it yet): one entry per condition,
  `{"scenario": {keyword arguments of circuits.scenario}, "pins": [(ref, pin, (min V,
  max V))]}`, the window each enable or output pin must sit in, with the datasheet
  threshold and its source beside it. Extend `scenario` in circuits.py to accept the
  condition's keywords (a rail at 0 V, a controller pin left floating). Then hand it to
  `add-check`, which writes the project check that reads the table and proves it fails
  by planting the missing pull in `mutants.py`.
- `SUPPLY_PINS`: the normal-case range of each enable and supply pin you traced.
- `GPIO_SCENARIOS` (ESP32-S3 boards): a row per condition where another device drives
  the board's pins while it is unpowered.
- `STRAP_SCENARIO` (ESP32-S3 boards): if the walk changed what reset looks like.

## Lane and hand-offs

This review answers what state each output is in. It does not size parts
(`review-derating`), trace current or return paths (`review-current-paths`), judge
connectors mated wrongly (`review-misplug`), plan the bench (`plan-bringup`) or audit
buying (`review-sourcing`). Note such a problem in one line under Out of scope. When the
same kind of finding turns up on a second board, it becomes a check: hand it to
`add-check` and take it out of this skill.
