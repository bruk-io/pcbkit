---
description: >-
  Review whether a pcbkit board can be bought and built as designed before an order:
  live stock and lifecycle per BOM line, named second sources, line consolidation,
  assembly cost drivers, connector variants and footprints against the ordered part
  number, do-not-populate parts, lead times and the fab's service tier. Load it before
  ordering boards, requesting an assembly quote, or advising whether a board can be
  bought and built, even a five-board batch; it runs before /pcbkit:order-pcbway.
  Without it Claude quotes stock and price from memory, counts BOM lines and parts that
  pcbkit quote and the outputs checks already count, silently merges deliberate part
  choices, and passes connector variants and placement rotations it never checked.
---

# Sourcing review

Gate: before ordering, after `pcbkit finalize` and a full `pcbkit check`, and before the
user starts `/pcbkit:order-pcbway`. Question: can this board be bought and built as
designed, in this batch, from this assembler?

## Hard rules

- **Never state stock, price, lifecycle, lead time or minimum order from memory.** Look
  each up at review time: dispatch the `parts-researcher` agent per line or small group,
  in parallel; it returns the distributor, the quantity break and the date.
- **Not verified live means `UNVERIFIED`, not fine.** It never appears as low risk.
- **Do not hard-code the assembler's rules.** Read its current capabilities page and
  quote; cite what you read and when.
- **Flag consolidation, never apply it.** Ask before calling a duplication an accident.

## 1. Start from what the checks and the quote decide

Review the files the fab receives, in `out/fab/`: the BOM (`<fab_name>_BOM.csv`), the
centroid and the Gerber zip. If `pcbkit quote` warns that the zip is older than the
board, or `results.json` is partial or failing, stop and say so: a review of stale files
is worse than none. Then take as given:

- `pcbkit quote --assembled N` (N the boards to assemble): unique lines, SMD placements,
  fine-pitch packages with their references, through-hole parts, copper, minimum track,
  space and hole. Never count these by reading.
- `test_bom_complete` (`outputs`): every fitted part has an orderable part number.
- `test_bom_matches_schematic`, `test_centroid_matches_board` (`outputs`): BOM,
  centroid, DNP and board agree.
- `test_annular_rings_and_drills_meet_pcbway`, `test_design_rules_cover_fab_limits`
  (`fab`, `PCBWAY`) and `test_job_file_stackup` (`outputs`): the fab's limits.
- `test_power_footprints_match_datasheet` (`fab`, `POWER_FOOTPRINTS`): the pads of
  power parts against their datasheets.
- `parts/<part>.datasheet.json` from `pcbkit datasheet find`: which datasheet is the
  ordered part's. A line that is not `VERIFIED` or `CONFIRMED` has an unchecked
  footprint.

## 2. Review

1. **Sort the BOM by risk** and spend effort top-down: single-source parts, regulators
   and other active silicon, connectors (variant-sensitive), leadless and fine-pitch
   packages, long-lead lines, lifecycle warnings. Generic passives last.
2. **Availability per line**: assembler stock, distributor stock, lifecycle, price at
   the batch quantity with the assembler's attrition, lead time, minimum order.
3. **Second sources** for every critical line: a named alternate, its compatibility
   class, and exactly what changes if it is fitted.
4. **Consolidation**: near-duplicates that could merge without changing behaviour.
   Ask whether the split is deliberate first.
5. **Assembly cost drivers and correctness**, **variants** (each DNP set safe to power
   and testable), **hand-soldered parts**, and **the fab tier**: what each means is in
   [references/checklist.md](references/checklist.md).

A generic worked example: [references/example.md](references/example.md).

## Output - fixed format

```markdown
# Sourcing review: <board> rev <rev> - <date>
Batch: <n> boards, <m> assembled, variants: <list>. Assembler: <name>. Exports reviewed: <out/fab files, timestamps>.

## BOM risk table
| Line | Refs | Part number | Source checked (where, date) | Stock | Lifecycle | Alternate (compat, what changes) | Risk |
|------|------|-------------|------------------------------|-------|-----------|----------------------------------|------|
Risk is HIGH / MED / LOW / UNVERIFIED, with a one-line reason. Sorted HIGH first.

## Consolidation proposals
- <lines to merge> -> <proposed part>. Saves <n> unique lines. Behaviour change: none / <what>. Deliberate? <asked / answer>.

## Assembly findings
- [BLOCKER|COST|CHECK] <ref or line>: <finding>. Evidence: <file, field, page read, or check>. Action: <fix>.

## Variant matrix
| Group | Refs | Variant A | Variant B | ... | Jumper/strap state | Safe and testable when absent? |
Plus the DNP list per variant, as the assembler will receive it.

## Unverified
Everything not checked live, and why.

## Written down
- <file and table>: <rows added or changed>
```

## 3. Write it down

- design.py: the `mpn` of each line settled, `dnp=True` for parts left off, and a
  comment beside a deliberate duplicate or a part with a named alternate ("same part as
  U2 on purpose"; "alternate: <mpn>, needs R5 changed"), so the next review does not
  ask again. bom.py `MPN_OVERRIDE` or `REF_OVERRIDE` where the BOM must name what is
  bought and the schematic something else.
- `POWER_FOOTPRINTS` for each power part whose tab, sense pads or drills you checked
  against the ordered part's datasheet; `CAP_VRATED` for a capacitor whose part number
  changed.
- `POLARITY_MARKS`, `FIDUCIALS` and `BOTTOM_PARTS` (proposed): hand them to `add-check`.
- For the order: parts not to substitute and parts the user supplies, for the notes
  `/pcbkit:order-pcbway` asks for. Never start the order: the user does.

## Lane and hand-offs

Electrical design is the other reviews' (`review-derating` for ratings, `review-misplug`
for connector choice). A kind of finding seen on a second board becomes a check
(`add-check`) and leaves this skill.
