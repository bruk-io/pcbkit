---
description: >-
  Review where a pcbkit board's large and fast currents flow and how they return -
  switching-regulator hot loops, load returns sharing ground with sensors, grounding and
  star points, inductors and switch nodes near an ADC input, crystal or antenna, bulk
  capacitor placement, current-driven heat and connector return contacts - and write
  the intent (victims, aggressors, hot loops, current segments, keep-clear regions) into
  specs.py so the copper checks measure it on every route. Load it after placement and
  before routing is final, and before answering a question about current paths, return
  paths, grounding, hot loops, noise coupling or where to place a converter, even one
  converter. Not the look at the routed copper itself (slivers, islands, a track over a
  cut pour): that is review-board. Without it Claude re-checks track widths and
  clearances (already checks), never tells the copper checks which net is the victim,
  judges hot loops without the datasheet's layout example, and forgets pcbkit boards
  have two layers.
---

# Current path review

The copper checks see the copper; they do not know which loop is hot or which signal is
the victim. This review supplies that intent, judges the placement against it, and
writes it down so every later route is measured. Gate: after `pcbkit build`, before
routing is final. pcbkit builds two-layer boards: the reference for a fast net is the
ground pour on the other side.

## 1. Start from the checks that apply at this gate

The script names any group pcbkit.toml leaves off (switch it on in `[checks] groups`)
and prints the gate's run with its caveats. Use the first for a placed board and the
second once it is routed, and run what it prints:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" --gate place
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" --gate route
```

Placed: netlist and part-position checks, plus `or test_<topic>` for a project check
that needs no tracks (`PART_SPACING`). Routed: every copper check too. Missing inputs
are not findings. Once routed these measure what this review writes; never judge by
eye what they measure:

| Question | Check (group) | Table |
|---|---|---|
| Track width and drop against current | `test_copper_segment` (`copper`) | `COPPER_SEGMENTS`, `COPPER_BUDGETS` |
| Farthest load's supply sag and ground lift | `test_load_supply_regulation` (`copper`) | `SUPPLY_REGULATION` |
| Sensitive net beside switching or high-current copper | `test_parallel_length`, `test_sensitive_nets_exist` (`copper`) | `COUPLING` |
| Sense pair loop area | `test_sense_pair_loop_area` (`copper`) | `SENSE_LOOPS` |
| Copper kept out of a region | `test_reserved_copper_stays_clear` (`copper`) | `RESERVED_COPPER` |
| Ground stitching gaps | `test_stitching_density` (`copper`) | `[stitch]` in pcbkit.toml |

## 2. Settle intent and gather the inputs

Ask for what is missing and say what it blocks; review the rest.

- design.py for switch nodes, sense lines and sensitive inputs; layout.py for
  positions; routing.py for net classes, hand routes, keep-outs and pours; `[stackup]`
  and `[stitch]` in pcbkit.toml.
- Pictures: `pcbkit shots --no-render`. Add a `SHOTS` region in layout.py around each
  converter and each victim, so the next look is one command.
- The layout example in each switching regulator's datasheet (`pcbkit datasheet find`,
  or the `parts-researcher` agent). Judge the hot loop against it, not a general rule.
- Load currents and edge rates from the user. Never infer peak currents from part numbers.
- **The paths**: the top 5 to 10 by peak current and separately by edge rate. Ask the
  user to confirm them and to name the victims: what must not see noise or ground shift.

## 3. Write the intent, then run what can run

- `COUPLING`: each victim under `sensitive` with its class and noise budget, each switch
  node, inductor or high-current path under `aggressors` with its kind and region, and
  `filters` for a victim with an RC filter.
- `SENSE_LOOPS` for every measurement pair; `DECOUPLING` for every capacitor whose place
  matters, with its distance limit and source.
- `COPPER_SEGMENTS` for each high-current path, supply and return, with terminals at the
  real pads; where a load return passes a sensor's ground, a ground segment with
  `heating` False and the sensor's ground pad as a terminal with share 0.
  `SUPPLY_REGULATION` for the farthest load. `COPPER_BUDGETS` comes from
  `review-derating`.
- `RESERVED_COPPER` for an antenna or a region under an inductor or a sensitive part,
  with the matching keep-out in routing.py; `[stitch] dense` boxes under converters.
- `PART_SPACING`, `HOT_LOOPS` (the loop's pads in order around it) and
  `REFERENCE_PLANES` (each fast net and the side its return runs on): proposed tables,
  which no built-in check reads yet; shapes in "Proposed tables" in
  `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`. Hand each to `add-check`.

Run the selection again: decoupling and filter-capacitor distances are decided when
placed; on a routed board the copper checks decide the rest now, and after every route.

## 4. Judge the placement

For each path write source, path, load, return, naming the side and the vias; if you
cannot write the return leg, that is the first finding. Judge each against
[references/physics.md](references/physics.md): hot loops against the datasheet example,
shared ground impedance, aggressors near victims, bulk capacitance at the load, heat,
connector return contacts. A generic worked review, for the shape of the table and of a
`COPPER_SEGMENTS` row: [references/example.md](references/example.md).

## Lane and hand-offs

Not this review: ratings (`review-derating`), fault states (`walk-fault-states`), plugs
(`review-misplug`). After routing, `review-board` looks at the copper itself.

## Output

Always this format, nothing before it but one line naming the inputs you had and lacked.

**Reviewed current paths**

| Path (source - load) | Peak current or edge rate | Return route | Verdict |
|---|---|---|---|

Return route in words: sides and vias. Verdict: `OK`, `Concern`, `Problem`, `Measured by
a check` (name it and its result) or `Cannot review` (the missing input).

**Findings**, most severe first. Each: location (references, net, side, layout mm);
problem (what the current does there); why it matters physically (loop area, ground
shift, coupling, heating, and the symptom); suggested change (layout.py position,
routing.py keep-out, pour or hand route).

**Written to specs.py**: the rows you added.
