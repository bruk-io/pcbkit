# Where to change what

A board project is a folder with a `pcbkit.toml`. You own the Python and the settings;
pcbkit writes `kicad/`, `out/`, `golden/` and `fab/`. Coordinates in layout.py and the
routing hooks are millimetres from the board's top-left corner, y pointing down.

## Which file for which change

| To change | Edit | Then run |
|---|---|---|
| a part, its value or footprint, a net, a whole circuit block | design.py (`part`, `R`, `C`, `LED`) | `pcbkit build`, then `route --eco golden` |
| where a part sits, its rotation, mounting holes, the board size or outline | layout.py (`P`, `W`, `H`, `HOLES`, `CORNER_R`, `outline`) | `pcbkit build`, then `route --eco golden` for a nudge, `route` for a rearrangement |
| track widths per net, hand-routed power paths, keep-outs, pours, ground links | routing.py | `pcbkit route` |
| silkscreen labels, hidden references, polarity marks, the title block | silk.py | `pcbkit finalize` |
| copper weight, thickness, router tries, stitching, which check groups run | pcbkit.toml | the stage the key belongs to |
| the part number or description a BOM line shows | `mpn` in design.py, or bom.py when the schematic and the BOM should differ | `pcbkit finalize` |
| a symbol or footprint KiCad does not have | footprints.py, footprints/*.kicad_mod | `pcbkit sch` |
| a datasheet limit or design number a check reads | specs.py (with its source beside it) | `pcbkit check` |
| a new check, or a planted mistake for one | checks/test_*.py, mutants.py (see add-check) | `pcbkit check`, `pcbkit mutants` |
| named crops for review pictures | `SHOTS` in layout.py | `pcbkit shots` |

A part in design.py with no entry in `P` is parked below the board and listed by `build`.
An entry in `P` for a part the design does not have is ignored.

## What pcbkit writes, and what to read

| File | What it is |
|---|---|
| `kicad/drc.rpt` | The latest DRC report, with positions (see kicad10-quirks.md for their offset). |
| `kicad/erc.rpt` | The ERC report from the last `sch` or `build`. |
| `kicad/freerouting.log` | The router's console output from the latest run. |
| `kicad/placed.kicad_pcb` | The placed board, before routing. |
| `kicad/prerouted.kicad_pcb`, `kicad/<stem>.dsn`, `kicad/<stem>.ses` | The pre-routed board, its router input and the router's result. `promote` copies these three into golden/. |
| `kicad/<stem>.kicad_pcb` | The finished board. |
| `out/fab/` | What goes to the fab house: the Gerber zip, BOM and centroid. |
| `out/docs/` | Schematic and assembly PDFs, copper PDFs, 3D renders. |
| `out/checks/results.json` | One entry per check: outcome, skip reason or failure message, recorded numbers. |
| `out/shots/` | Crops and renders from `pcbkit shots`. |

pcbkit overwrites what it makes and never deletes a file it no longer makes: delete
`kicad/` for a clean rebuild (golden/ keeps the route).
