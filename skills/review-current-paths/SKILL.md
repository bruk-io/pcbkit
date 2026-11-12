---
description: >-
  Review where a pcbkit board's large and fast currents flow and how they return -
  switching-regulator hot loops, returns across slots and cut pours, ground shared
  between motor or load returns and sensors, inductors and switch nodes near an ADC
  input, crystal or antenna, bulk capacitor placement, current-driven heat and
  connector return pins - and write the intent (victims, aggressors, hot loops, current
  segments, keep-clear regions) into specs.py so the copper checks measure it on every
  route. Load it after placement and before routing is final, and before reviewing,
  planning or advising on a board's layout, even one converter or a small board.
  Without it Claude re-checks track widths and clearances (already checks), never
  tells the copper checks which net is the victim, judges hot loops without the
  datasheet's layout example, and forgets pcbkit boards have two layers.
---

# Current path review

The copper checks see the copper; they do not know which loop is hot or which signal is
the victim. This review supplies that intent, judges the placement against it, and
writes it down so every later route is measured. Gate: after `pcbkit build`, before
routing is final. pcbkit builds two-layer boards: the reference for a fast net is the
ground pour on the other side, cut by every track routed there.

## 1. Start from what the checks decide

If the board has been routed, run `.venv/bin/pcbkit check` and read
`out/checks/results.json`. Never judge by eye what these measure; read their numbers:

| Question | Check (group) | Table |
|---|---|---|
| Track width and drop against current | `test_copper_segment` (`copper`) | `COPPER_SEGMENTS`, `COPPER_BUDGETS` |
| Farthest load's supply sag and ground lift | `test_load_supply_regulation` (`copper`) | `SUPPLY_REGULATION` |
| Sensitive net beside switching or high-current copper | `test_parallel_length`, `test_sensitive_nets_exist` (`copper`) | `COUPLING` |
| Filter capacitor at the pin it protects | `test_filter_caps_sit_by_the_adc_pin` (`copper`) | `COUPLING["filters"]` |
| Sense pair loop area | `test_sense_pair_loop_area` (`copper`) | `SENSE_LOOPS` |
| Copper kept out of a region | `test_reserved_copper_stays_clear` (`copper`) | `RESERVED_COPPER` |
| Ground stitching gaps | `test_stitching_density` (`copper`) | `[stitch]` in pcbkit.toml |
| Decoupling capacitor near its pin | `test_decoupling_caps_are_close` (`fab`) | `DECOUPLING` |
| Clearances | `test_drc_clean_with_schematic_parity` (`kicad`) | none |

What no check measures yet, and this review judges: regulator hot-loop area and fast
nets crossing a slot in their reference pour (both planned with four-layer support),
heat sources beside sensitive parts, and connector return contacts.

## 2. Gather the inputs

Ask for what is missing and say what it blocks; review the rest.

- design.py for which nets are switch nodes, sense lines and sensitive inputs;
  layout.py for positions; routing.py for net classes, hand routes, keep-outs and pours;
  `[stackup]` and `[stitch]` in pcbkit.toml.
- Pictures: `pcbkit shots --no-render` after a build or route. Add a `SHOTS` region in
  layout.py around each converter and each victim, so the next look is one command.
- The layout example in each switching regulator's datasheet (`pcbkit datasheet find`,
  or the `parts-researcher` agent). Judge the hot loop against it, not a general rule.
- Load currents and edge rates from the user. Never infer peak currents from part numbers.

## 3. Review

1. **List the top 5 to 10 paths**, by peak current and separately by edge rate (switch
   nodes, gate drives, clocks, fast PWM). Ask the user to confirm the list and to name
   the victims: what on this board must not see noise or ground shift.
2. **Write each path as source, path, load, return**, naming the side and the vias.
   If you cannot write the return leg, that is the first finding.
3. **Judge each** against [references/physics.md](references/physics.md): hot loops,
   returns under cut pours, shared ground impedance, aggressors near victims, bulk
   capacitance at the load, heat, connector return contacts. A generic worked review is
   [references/example.md](references/example.md); read it before a first review.

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

## 4. Write the intent down

So the next route is measured, not reviewed:

- `COUPLING`: each victim under `sensitive` with its class and noise budget, each switch
  node, inductor or high-current path under `aggressors` with its kind and region, and
  `filters` for a victim with an RC filter.
- `SENSE_LOOPS` for every measurement pair; `DECOUPLING` for every capacitor whose place
  matters, with its distance limit and source.
- `COPPER_SEGMENTS` for each high-current path, supply and return, with terminals at the
  real pads; a ground segment with `heating` False where a load return passes a
  sensor's ground; `SUPPLY_REGULATION` for the farthest load. `COPPER_BUDGETS` comes
  from `review-derating`.
- `RESERVED_COPPER` for an antenna, a region under an inductor or a sensitive part, with
  the matching keep-out in routing.py; `[stitch] dense` boxes under converters.
- `PART_SPACING`, `HOT_LOOPS` and `REFERENCE_PLANES` are proposed tables
  (`${CLAUDE_PLUGIN_ROOT}/plans/wp16-review-skills.md`): write the intent, and hand
  `PART_SPACING` to `add-check`; the other two wait on four-layer support.

## Lane and hand-offs

Not this review: ratings and margins (`review-derating`), fault states
(`walk-fault-states`), plugs (`review-misplug`). After routing, `review-board` looks at
the copper itself: slivers, islands, signals under switchers. A kind of finding seen on
a second board becomes a check (`add-check`) and leaves this skill.
