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
  merges deliberate part choices, and passes connector variants it never checked.
---

# Sourcing review

Can this board be bought and built as designed, in this batch, from this assembler?
Gate: after `pcbkit finalize`, before `/pcbkit:order-pcbway`; `board-workflow` sequences it.
Ratings are `review-derating`'s, connector choice `review-misplug`'s.

## Hard rules

- **Never state stock, price, lifecycle, lead time or minimum order from memory.**
  Dispatch `parts-researcher` per line or small group, in parallel, for the distributor,
  quantity break and date.
- **Not verified live means `UNVERIFIED`, not fine.** It never appears as low risk.
- **Do not hard-code the assembler's rules**: read its current pages; cite them, dated.
- **Flag consolidation, never apply it.** Ask before calling a duplication an accident.

## 1. Start from what the checks and the quote decide

Review the files the fab receives in `out/fab/`. The script names any group pcbkit.toml
leaves off. `board-workflow` switches every group on before `pcbkit finalize`; one that
is off here, switched on now, needs `pcbkit finalize` only (not a new route), then start
again. If the preflight exits non-zero, the files or results are stale, partial or
failing: show what it printed and stop, as a review of stale files is worse than none.

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" --gate order
.venv/bin/pcbkit check
python3 "${CLAUDE_PLUGIN_ROOT}/skills/order-pcbway/scripts/preflight.py" --before-sourcing
python3 "${CLAUDE_SKILL_DIR}/scripts/leadless.py"
python3 "${CLAUDE_SKILL_DIR}/scripts/bomlines.py" --boards M --spare S
```

Then take as given:
- `pcbkit quote --assembled N` (N the boards to assemble): unique lines, SMD placements,
  through-hole parts, copper, minimum track, space and hole, and the BGA, QFP and QFN
  parts, found by those letters anywhere in the footprint id. `leadless.py` lists the
  DFN, SON, LGA and chip-scale lines that count misses.
- `outputs` (`test_bom_complete`, `test_bom_matches_schematic`,
  `test_centroid_matches_board`, `test_job_file_stackup`) and `fab` (the PCBWay limits,
  `test_power_footprints_match_datasheet`): BOM, centroid and board agree, limits hold.
- `bomlines.py` (M the boards to assemble, S the spare parts per line the assembler's
  page asks for): each line's quantity to buy, and `unchecked`, the lines whose
  `parts/<part>.datasheet.json` from `pcbkit datasheet find` is not `VERIFIED` or
  `CONFIRMED`: an unchecked footprint.

## 2. Write the decidable rows, then run

- `POLARITY_MARKS`, `BOTTOM_PARTS`, and `FIDUCIALS` if the assembler asks for them:
  proposed tables no built-in check reads yet (shapes in "Proposed tables",
  `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`). Hand each to `add-check`.
- Near-duplicate values (one package, within a few percent): `add-check` writes a
  project check that lists them from the BOM and never fails: a split may be deliberate.

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
   [references/checklist.md](references/checklist.md). For a deliberate duplicate or a
   variant trap: [references/example.md](references/example.md).

## 4. Write it down, then stamp

- specs.py: `POWER_FOOTPRINTS` for each power part whose pads match the ordered part's
  datasheet, `CAP_VRATED` for a capacitor whose part number changed.
- A settled change to design.py or bom.py (an `mpn`, a `dnp`, an override, a comment
  beside a deliberate duplicate or an alternate) changes the fab files: make it, take
  the board to `pcbkit finalize` again, and review the new files from step 1.
- For the order: parts not to substitute and parts the user supplies, for the notes
  `/pcbkit:order-pcbway` asks for. Never start the order: the user does.
- Last, write `sourcing.md` (below), then stamp the BOM and centroid it judged with
  `python3 "${CLAUDE_SKILL_DIR}/scripts/stamp.py"`: the order refuses any other files.
  It refuses a `sourcing.md` older than the BOM or the centroid, and stamps the BOM in
  both formats.

## Output - fixed format

Write it to `sourcing.md` in the project folder (never `out/` or `fab/`), replacing any
earlier one, and show it in the reply.

The format, with each section's columns: [references/output.md](references/output.md).
