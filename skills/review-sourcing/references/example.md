# Worked example: my-board

A generic CAN-bus sensor node, made up to show how the review reasons. Batch: ten
boards, all assembled. It records no stock or price: every availability figure is
looked up again at review time.

## What the quote and the checks already said

`pcbkit quote --assembled 10` gave 31 unique lines, 84 SMD placements, one QFN part
(U1) and two through-hole parts (J1, J2). The BOM search for leadless packages the quote
does not count found U4 in a DFN-8 and U9 in an LGA-8. `test_bom_complete`,
`test_bom_matches_schematic` and `test_centroid_matches_board` passed. None of that is
counted again.

## Recognise deliberate consolidation

U3 and U7 are the same quad op-amp in a TSSOP-14, though U7's circuit uses only two of
its four sections; a dual in a smaller package would do there.

- Do **not** propose the dual. One op-amp line across the board was chosen on purpose:
  one fewer unique line and one part to stock. Note it as recognised, and check there
  is a design.py comment saying so.
- It makes the op-amp line HIGH attention: two circuits on every board depend on it.
  Look up assembler stock for 10 boards x 2 plus attrition, distributor stock and
  lifecycle (`parts-researcher`).
- Correctness that comes with it: U7's two spare sections must be tied off the way the
  datasheet recommends (a follower to a mid-rail node, say), not left floating. Check
  the netlist, and say so if they float.
- Name a pin-compatible alternate only after reading both datasheets: pinout, supply
  range, input common-mode range, output swing, and any difference in the spare
  sections' recommended tie-off.

## Variant matrix

| Group | Refs | End node | Middle node | Jumper/strap state | Safe and testable when absent? |
|---|---|---|---|---|---|
| Core | all core refs | fitted | fitted | - | n/a |
| Bus termination | R20 (120 ohm across CANH and CANL) | fitted | DNP | - | Check: a bus with every node terminated reads about 120 / n ohm and fails at speed; a bus with none rings |

R20 is the trap: one BOM fits it on every board, and a bus of ten terminated nodes does
not work. The order notes must say which boards get it, or the batch is ordered as two
variants with their own DNP lists.

## Through-hole parts on a surface-mount board

J1 and J2, the two bus connectors, are through-hole; everything else is surface mount
on the top. Read the assembler's current THT pricing and choose: fitted by the
assembler, or `dnp=True` and soldered by hand. If by hand, check the iron reaches both
past the neighbouring parts and that the ground pins have thermal relief.

## Connectors to confirm

For J1 and J2, against the maker's ordering-code table, not inferred: the entry and
mount of the variant intended, that the footprint matches it (pegs, pitch, pin count),
that the assembler can source that exact part number, and the mating plug's part
number recorded for making cables.

## Written down

- design.py: a comment beside U7 ("same part as U3 on purpose; sections C and D tied
  off") and the alternate's part number beside U3; R20 `dnp=True` with the order notes
  naming the two end-node boards.
- `POWER_FOOTPRINTS`: U1's exposed pad as the tab, on `GND`.
- `POLARITY_MARKS = ["D1", "J1", "J2", "U1", "U3", "U4", "U7", "U9"]`, a proposed table
  for `add-check`.
