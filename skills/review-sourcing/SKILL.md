---
description: >-
  Review whether a pcbkit board can be bought and built as designed: live stock and
  lifecycle per BOM line, named second sources, line consolidation, assembly cost
  drivers, connector variants and footprints against the ordered part number,
  do-not-populate variants, lead times - and record the dated result in sourcing.md.
  Load it when asked whether the parts can be bought, are in stock or have second
  sources, before requesting an assembly quote, or when board-workflow sequences it
  before an order, even for a five-board batch. Without it Claude quotes stock and
  price from memory, counts lines and packages that pcbkit quote and the outputs checks
  already count, trusts the quote's package count to cover every leadless part,
  silently merges deliberate part choices, and passes connector variants it never
  checked.
---

# Sourcing review

Can this board be bought and built as designed, in this batch, from this assembler?
Gate: after `pcbkit finalize`, before `/pcbkit:order-pcbway`; `board-workflow` sequences it.

## Hard rules

- **Never state stock, price, lifecycle, lead time or minimum order from memory.**
  Dispatch `parts-researcher` per line or small group, in parallel, for the distributor,
  quantity break and date. pcbkit's Mouser client (`pcbkit/mouser.py`, `pcbkit/parts.py`)
  has no command yet; a stock command (WP17) will replace the agent for Mouser's figures.
- **Not verified live means `UNVERIFIED`, not fine.** It never appears as low risk.
- **Do not hard-code the assembler's rules**: read its current pages; cite them, dated.
- **Flag consolidation, never apply it.** Ask before calling a duplication an accident.

## 1. Start from what the checks and the quote decide

Everything exists at this gate, so every group applies: run `.venv/bin/pcbkit check`
with no `-k`. Review the files the fab receives, in `out/fab/`: the BOM
(`<fab_name>_BOM.csv`), the centroid and the Gerber zip. If `pcbkit quote` warns that
the zip is older than the board, or `results.json` is partial or failing, stop and say
so: a review of stale files is worse than none. Then take as given:

- `pcbkit quote --assembled N` (N the boards to assemble): unique lines, SMD placements,
  through-hole parts, copper, minimum track, space and hole, and the BGA, QFP and QFN
  parts, which it finds by those letters in the footprint name and never by pitch.
  DFN, SON, LGA and wafer-level packages are not in that count: list them from the
  BOM's `Package / Footprint` column with a search, not by reading.
- `outputs` (`test_bom_complete`, `test_bom_matches_schematic`,
  `test_centroid_matches_board`, `test_job_file_stackup`) and `fab` (the PCBWay limits,
  `test_power_footprints_match_datasheet`): BOM, centroid and board agree, limits hold.
- `parts/<part>.datasheet.json` from `pcbkit datasheet find`: a line that is not
  `VERIFIED` or `CONFIRMED` has an unchecked footprint.

## 2. Write the decidable rows, then run

- `POLARITY_MARKS`, `BOTTOM_PARTS`, and `FIDUCIALS` if the assembler asks for them:
  proposed tables no built-in check reads yet (shapes in "Proposed tables",
  `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`). Hand each to `add-check`.
- Near-duplicate values (resistors or capacitors of one package within a few percent):
  `add-check` writes a project check that lists them from the BOM and records them; it
  never fails, since only the user can say whether a split is deliberate.

Run `pcbkit check` in full again. A failed row is an assembly finding.
## 3. Judge what needs the market and the user

1. **Sort the BOM by risk** and spend effort top-down: single-source parts, active
   silicon, connectors, leadless and fine-pitch packages, long-lead lines, lifecycle
   warnings. Generic passives last.
2. **Availability per line**: assembler stock, distributor stock, lifecycle, price at
   the batch quantity with attrition, lead time, minimum order (`parts-researcher`).
3. **Second sources** for every critical line: a named alternate, its compatibility
   class, and exactly what changes if it is fitted.
4. **Consolidation**: from the near-duplicate list, which could merge without changing
   behaviour; ask whether each split is deliberate first.
5. **Assembly cost and correctness**, **variants** (each DNP set safe to power and
   testable), **connector variants**, **hand-soldered parts**:
   [references/checklist.md](references/checklist.md).

For a deliberate duplicate or a variant trap: [references/example.md](references/example.md).

## 4. Write it down

- design.py: each settled `mpn`, `dnp=True` for parts left off, and a comment beside a
  deliberate duplicate or a named alternate, so the next review does not ask again.
  bom.py `MPN_OVERRIDE` or `REF_OVERRIDE` where the BOM must differ from the schematic.
- `POWER_FOOTPRINTS` for each power part whose pads you checked against the ordered
  part's datasheet; `CAP_VRATED` for a capacitor whose part number changed.
- For the order: parts not to substitute and parts the user supplies, for the notes
  `/pcbkit:order-pcbway` asks for. Never start the order: the user does.

## Lane and hand-offs

Ratings are `review-derating`'s, connector choice `review-misplug`'s.

## Output - fixed format

Write it to `sourcing.md` in the project folder (never `out/` or `fab/`), replacing any
earlier one, and show it in the reply. The exports line names each `out/fab/` file
with its modification time as `ls -l out/fab` shows it: `order-pcbway` compares them.

```markdown
# Sourcing review: <board> rev <rev> - <date>
Batch: <n> boards, <m> assembled, variants: <list>. Assembler: <name>.
Exports reviewed: <file> <modification time>, one per out/fab file.

## BOM risk table
| Line | Refs | Part number | Source checked (where, date) | Stock | Lifecycle | Alternate (compat, what changes) | Risk |
Risk is HIGH / MED / LOW / UNVERIFIED, with a one-line reason. Sorted HIGH first.

## Consolidation proposals
- <lines to merge> -> <proposed part>. Saves <n> lines. Behaviour change: none / <what>. Deliberate? <answer>.

## Assembly findings
- [BLOCKER|COST|CHECK] <ref or line>: <finding>. Evidence: <file, field, page, or check>. Action: <fix>.

## Variant matrix
| Group | Refs | Variant A | Variant B | ... | Jumper/strap state | Safe and testable when absent? |

## Unverified
Everything not checked live, and why.

## Written down
- <file and table>: <rows added or changed>
```
