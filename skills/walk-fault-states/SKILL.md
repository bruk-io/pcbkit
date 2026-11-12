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

What each output and rail enable does in each abnormal condition. Gate: after `pcbkit
sch` is ERC-clean, before layout. Where a cell is a DC solve or a graph walk, a check
decides it; the review supplies intent, datasheet facts and the judgement on what follows.

## 1. Start from the checks that apply at this gate

At the schematic gate only the netlist exists, so only these apply: the `circuit` and
`esp32s3` groups and `test_erc_clean` (`kicad`), plus project checks that read only the
netlist and specs.py. Run exactly those:

```
.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"
```

Add `or test_<topic>` for each such project check (a previous run's
`test_fault_states`, say). `outputs`, DRC and `copper` need a board or fab files that do
not exist yet: a check whose inputs do not exist is not a finding. A `-k` run leaves
partial results (`selection.complete` false); the order gate runs all. A gate option
(WP17, --gate sch) will replace this selection. Take as given, and report a failure
rather than re-derive it:

- Floating and unconnected pins: `test_erc_clean`. An open pin with a no-connect flag
  in design.py was left open on purpose.
- Supply pins inside their range at `SUPPLY_SCENARIO`: `test_supply_pins_in_range`.
- ESP32-S3 DevKit boards: strapping pins at reset (`test_strapping_pins_at_reset`), and
  every GPIO under its absolute maximum in each `GPIO_SCENARIOS` row, which is how
  injected current from another powered device is judged.
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

The rows are proposed tables: no built-in check reads them yet. Their shapes are in
"Proposed tables" in `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

1. `FAULT_STATES`: one entry per condition a DC solve can show, with the keyword
   arguments of `circuits.scenario` that make it (a rail at 0 V, controller pins
   floating with their datasheet reset pulls) and the window each enable or output pin
   must sit in, from the datasheet threshold, its source beside it. Extend `scenario`
   in circuits.py for keywords it lacks.
2. `POWER_PATHS`: which supply may reach which rail and through which parts (USB onto
   the logic rail only, say). Which rails USB reaches is a graph walk, not a trace.
3. `GPIO_SCENARIOS` (ESP32-S3): a row per condition where another device drives the
   board's pins while it is unpowered.
4. Hand each proposed table to `add-check`, which writes the project check, proves it
   fails (a `mutants.py` entry: the missing pull) and runs it with the selection above.
   Each failed row is a cell that is not where intent wants it.

## 4. Judge what the checks cannot

- **Timing and order**: brownout, supply removed under load and hot-plug cells turn on
  which part crosses its threshold first; trace them from the datasheets.
- **Who holds it**: an off that only firmware maintains is not off when firmware
  crashes, resets or sits in the bootloader. Mark which cells hold in hardware.
- **Consequence and remedy** for every cell that is not off where intent says off:
  what the mechanism does, and a fix for this design.py from
  [references/patterns.md](references/patterns.md).

For the shape of the table and the rows, when unclear, see a finished walk of a generic
board: [references/example.md](references/example.md).

## 5. Write the rest down

`SUPPLY_PINS` for each pin traced in the normal case; `STRAP_SCENARIO` (ESP32-S3) if
the walk changed what reset looks like.

## Lane and hand-offs

Not this review: sizing parts (`review-derating`), current and return paths
(`review-current-paths`), plugs mated wrongly (`review-misplug`), the bench
(`plan-bringup`), buying (`review-sourcing`). Note such a problem under Out of scope.

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
