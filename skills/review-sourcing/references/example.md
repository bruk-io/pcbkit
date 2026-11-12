# Worked example: my-board

A generic board, made up to show how the review reasons. Batch: five boards, all
assembled. It records no stock or price: every availability figure is looked up again
at review time.

## What the quote and the checks already said

`pcbkit quote --assembled 5` gave 23 unique lines, 61 SMD placements, two QFN parts
(U2, U5) and four through-hole parts (J1, J10-J13 as a range). `test_bom_complete`,
`test_bom_matches_schematic` and `test_centroid_matches_board` passed. None of that is
counted again; the review starts from it.

## Recognise deliberate consolidation

U2 and U5 are the same buck regulator in a small leadless package, one making 12 V and
one 5 V, with the same inductor part number. The 5 V rail could use a smaller, cheaper
part.

- Do **not** propose the cheaper part. One regulator design and one inductor across both
  rails was chosen on purpose: fewer unique lines and one layout to debug. Note it as
  recognised, not as a finding, and check there is a design.py comment saying so.
- Treat the regulator line as HIGH attention: two blocks on every board depend on one
  leadless part. Look up assembler stock for 5 boards x 2 plus attrition, distributor
  stock and lifecycle (`parts-researcher`).
- Name a pin-compatible sibling as the fallback only after reading both datasheets:
  pinout and exposed pad, reference voltage, switching frequency and compensation,
  whether the shared inductor and output capacitance suit it, enable behaviour.
- The single inductor line is HIGH too: it is now one point of supply failure for both
  rails.
- Cost driver: a leadless part on every board. Read the assembler's page on X-ray
  inspection for QFN parts: included or charged.

## Variant matrix

| Group | Refs | Full | No sensor block | Jumper/strap state | Safe and testable when absent? |
|---|---|---|---|---|---|
| Core | all core refs | fitted | fitted | - | n/a |
| Sensor block | U6, R30-R33, C30 | fitted | DNP | JP1 bridged when U6 is absent, open when fitted | Check: the bus keeps its pull-ups in both states; who bridges JP1, and its default |

JP1 is the trap: a default that suits one variant leaves the other with a broken or
doubled bus, so the order notes must say who sets it.

## Through-hole parts on a surface-mount board

J1 and the sensor headers are through-hole; everything else is surface mount on the top.
Read the assembler's current THT pricing and choose: fitted by the assembler, or
`dnp=True` and soldered by hand. If by hand, J1 sits on the power pour: check its thermal
relief and that an iron reaches it past the headers.

## Connectors to confirm

For each connector line, against the maker's ordering-code table, not inferred: the
entry (top or side) and mount of the variant intended, that the footprint matches it
(tabs, bosses, pitch, pin count), that the assembler can source that exact part number,
and the mating housing and crimp part numbers recorded for making cables. J1's polarity
mark on the silkscreen is checked against the physical part.

## Written down

- design.py: a comment beside U5 ("same part as U2 on purpose") and the fallback's part
  number beside U2.
- `POWER_FOOTPRINTS`: U2 and U5's exposed pad as the tab, on `GND`.
- `POLARITY_MARKS = ["D1", "D2", "J1", "U1", "U2", "U5"]`, a proposed table for
  `add-check`.
