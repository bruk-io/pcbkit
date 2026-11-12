---
description: >-
  Walk a pcbkit board's circuit through every abnormal power and control condition
  (logic rail missing, controller in reset or its bootloader, firmware hung, USB-only
  power, brownout, safety input open, back-feed from another powered device, supply
  pulled under load, hot-plug) and state what every actuator output and rail enable does in
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

What each output and rail enable does in each abnormal condition. Gate: after `pcbkit
sch` is ERC-clean, before layout. Where a cell is a DC solve or a graph walk, a check
decides it; the review supplies intent, datasheet facts and the judgement on what follows.

## 1. Start from the checks that apply at this gate

At the schematic gate only the netlist exists: the `kicad` and `circuit` groups apply
(and `esp32s3` on an ESP32-S3 DevKit board), with project checks that read only the
netlist. Switch on in `[checks] groups` of pcbkit.toml any group the first line says is
off, then run:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" kicad circuit
.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"
```

A group just switched on fails by name each table it lacks: write those first. Add
`or test_<topic>` per such project check (a previous run's `test_fault_states`). A check
whose inputs do not exist yet (`outputs`, DRC, `copper`) is not a finding; a `-k` run is
partial, and the order gate runs all. Take as given, and report a failure rather than
re-derive it:

- Floating and unconnected pins: `test_erc_clean`. An open pin with a no-connect flag
  in design.py was left open on purpose.
- Supply pins inside their range at `SUPPLY_SCENARIO`: `test_supply_pins_in_range`.
- ESP32-S3 DevKit boards: strapping pins at reset (`test_strapping_pins_at_reset`), and
  each GPIO under the powered chip's 3.6 V in each `GPIO_SCENARIOS` row. The limit is
  fixed, so a board at 0 V back-powered through its pins passes: that is step 4's.
- A project check reading `FAULT_STATES` or `POWER_PATHS`: its rows are decided.

## 2. Settle intent and gather the facts

- **Inventory**: every actuator-facing output (driver outputs, PWM, gate drives, relay
  coils) and rail enable (regulator EN, load switch ON, driver output-enable or reset),
  with the signal that controls each, from design.py.
- **Conditions**: every row of [references/conditions.md](references/conditions.md),
  then ask the user for the board's own (a charger, a debug probe that powers the board).
- **Intent per output**: what it must do when the board is not in control. If no one
  has said, assume off for anything that moves or heats, and say so at the top.
- **Pin facts**: `pcbkit datasheet find` on each part on an output or enable path, and
  `parts-researcher` per fact (pull direction and strength, thresholds, UVLO, state in
  reset and bootloader), never memory. A fact no datasheet gives makes cells `undefined`.

## 3. Write the rows, then let the checks decide

The rows are proposed tables: no built-in check reads them yet. Their shapes and the
rule each check must apply are in "Proposed tables" in
`${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

1. `FAULT_STATES`: one entry per condition a DC solve can show, with the keyword
   arguments of `circuits.scenario` that make it (a rail fixed at 0 V, controller pins
   left unfixed with their datasheet reset pulls) and, per enable or output pad (a pad
   number, never a pin name), the window from the datasheet threshold, its source
   beside it. A floating pad fails whatever its window. Extend `scenario` in
   circuits.py for keywords it lacks.
2. `POWER_PATHS`: the supply nets, which may reach which and through which parts (USB
   onto the logic rail only, say), and each regulator's directed path in `parts`.
3. Hand each table to `add-check`, which writes the project check, proves it fails (a
   `mutants.py` entry: the missing pull) and runs it with the selection above. Each
   failed row is a cell that is not where intent wants it.
4. `SUPPLY_PINS` for each pin traced in the normal case; `STRAP_SCENARIO` (ESP32-S3)
   if the walk changed what reset looks like.

## 4. Judge what the checks cannot

- **Timing and order**: brownout, supply removed under load and hot-plug cells turn on
  which part crosses its threshold first; trace them from the datasheets.
- **Who holds it**: an off that only firmware maintains is not off when firmware
  crashes, resets or sits in the bootloader. Mark which cells hold in hardware.
- **Back-powering** (condition 8): current from another powered device through input
  ESD diodes into this board's unpowered rail. No check models it: trace it against each
  part's injection-current limit (`parts-researcher`).
- **Consequence and remedy** for every cell that is not off where intent says off:
  what the mechanism does, and a fix for this design.py from
  [references/patterns.md](references/patterns.md). A finished walk of a generic board,
  for the shape of the table and the rows: [references/example.md](references/example.md).

## Lane and hand-offs

Not this review: sizing parts (`review-derating`), current paths (`review-current-paths`),
plugs (`review-misplug`), the bench (`plan-bringup`), buying (`review-sourcing`). Note such
a problem under Out of scope.

## Output

Always this shape, nothing before it but one line naming the files and datasheets read.

**State table**: conditions as rows, outputs and rail enables as columns. Each cell is
exactly one of `off` (de-energised, held there by hardware), `on` (energised or able to
energise the actuator), `undefined` (floating, indeterminate or not traceable) or
`depends` (decided by something off the board or by timing; the footnote says what).
Footnote any cell whose reason is not obvious, with the check or datasheet fact behind
it.

**Findings**, numbered. Every `undefined` cell, and every `on` or `depends` cell where
intent says off: the cell, what happens and why (the failed check or the datasheet
fact), the consequence for the mechanism, a fix specific to this design.py, and whether
the fix takes firmware out of the safety path.

**Measured by a check**: one line per check that bears on the walk, with its result.
**Written to specs.py**: the rows added. **Out of scope**: one line each.
