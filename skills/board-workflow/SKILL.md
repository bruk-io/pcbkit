---
description: >-
  Run a pcbkit board project through its build, route, promote and finalize loop and read
  what comes back. Use whenever the user wants to build, place, route, re-route, finalize
  or export a board, fix DRC or ERC findings, unconnected pads or clearance errors, run
  pcbkit check or mutants and understand a failing result, choose between an eco and a
  full route, or asks which file to change for something (circuit, positions, routing,
  labels, stackup) in a folder that has a pcbkit.toml. Also the skill to use when the user
  asks to order boards: ordering is something the user starts with /pcbkit:order-pcbway,
  so point them to it. Writing a new check is add-check; starting a board is new-board.
---

# Working a pcbkit board

A board project is Python plus a `pcbkit.toml`; pcbkit generates everything else. Commands
find the project from the current folder upwards. `build`, `route`, `promote`, `finalize`,
`check`, `mutants` and `compare` need KiCad's pcbnew, so run them from the project as
`.venv/bin/pcbkit <command>` (`pcbkit setup` makes the `.venv`): a bare `pcbkit` outside it
answers "pcbnew isn't importable here". `pcbkit doctor` says what the machine lacks and
how to fix it.

## The loop

Each command's `--help` says what it does; this is when to run it and what to do with the
result.

| Command | Run it when | If it fails or surprises |
|---|---|---|
| `pcbkit sch` | only design.py changed and you want ERC or the netlist | Exit 1 is ERC errors: fix them in design.py. Warnings are listed and do not fail it. |
| `pcbkit build` | design.py, layout.py or pcbkit.toml changed | A part listed as "missing" has no position in `P` of layout.py: give it one. It replaces the board in kicad/, routed copper included. |
| `pcbkit route` | after a build | Exit 1: no try was clean and the least bad board is left in kicad/ (see Reading DRC). |
| `pcbkit route --eco golden` | a local change after a promoted route | If the router stalls it routes the whole board instead and says so. |
| `pcbkit promote` | the route is DRC 0/0/0 and you have looked at it | It refuses a route that is not clean. |
| `pcbkit finalize` | after every promote, and to refresh the fab files | If DRC is not clean it exports nothing and exits 1. |
| `pcbkit check` | after finalize; netlist-only checks work once `sch` or `build` has run | Exit 1: read each failure message in out/checks/results.json. Do not loosen the check. |
| `pcbkit mutants` | after adding or changing a check | Exit 1: a planted mistake was MISSED, so the check is blind to it. Exit 2: the control run failed, so nothing else means anything. |

`pcbkit report` writes the validation report from the last `check` run. `pcbkit compare
OLD NEW` shows what a re-route moved. `pcbkit shots` writes pictures of the board to
out/shots/ (see review-board). `pcbkit quote` prints the fab order numbers.

## Reading DRC

`route`, `promote` and `finalize` print `DRC: N violations, N unconnected pads, N
footprint errors` and a count per category; the full list with positions is
`kicad/drc.rpt`. Positions there are KiCad file millimetres: subtract 50 from x and y for
layout.py coordinates. `promote` and `finalize` accept only 0/0/0. `route` stops on the
copper categories:

| Category | Usually means | First move |
|---|---|---|
| `unconnected_items` | the router could not finish a net, or a pre-route or keep-out walls it in | Rerun with more `--tries`. If the same net fails every time, give it room in layout.py, or check the wide net classes and keep-outs in routing.py. |
| `clearance`, `shorting_items`, `tracks_crossing`, `hole_clearance` | a hand pre-route or keep-out in routing.py crowds something, or two parts sit too close | Read the position in drc.rpt, then change routing.py or layout.py. The router follows the rules; the hooks do not. |
| `copper_edge_clearance` | a part or track is too near the board edge | Move the part in layout.py, or enlarge the board. |
| footprint errors | board and schematic disagree on a part | Fix the footprint in design.py and `pcbkit build` again; the board is built from the netlist. |

Any other category (silkscreen, courtyard) names its cause in drc.rpt.

## Eco or full route

Use `--eco golden` when the change is local (a part nudged, a value changed, one net
added): it keeps the route you already reviewed. A full `pcbkit route` starts a new layout
that has to be reviewed again, so keep it for placement that really changed (many parts
moved, the outline resized) or an eco route that will not come clean. Either way, `promote`
then `finalize`.

## Boundaries

- Never edit kicad/, out/, golden/ or fab/ by hand: the next build overwrites them. A hook
  blocks the Edit and Write tools there. It cannot see Bash, so a heredoc or `sed -i` goes
  straight through: do not use them there either. Look at a board in KiCad if you like, but
  never save from it.
- Never loosen, skip or delete a check to get a green run. A failing check is the
  information; fix the design, or use add-check if the limit itself is wrong.
- Ordering from a fab house is started by the user with `/pcbkit:order-pcbway`, never by
  Claude. If asked to order, say so and point them to it: it begins by checking that the
  fab files and the check results are current.

## References

- [project-layout.md](references/project-layout.md): which file to change for what, and
  which files to read.
- [kicad10-quirks.md](references/kicad10-quirks.md): what KiCad 10 and Freerouting still
  do that pcbkit cannot hide.
- The full file contracts: `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.
