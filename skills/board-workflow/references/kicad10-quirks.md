# What KiCad 10 and Freerouting still do

Only what pcbkit cannot hide from you. The hazards it can shield are shielded in code and
need no care here; the one exception is a routing.py hook that calls pcbnew directly, which
gets none of that: use the `api` helpers it is handed.

## Positions in a DRC report are file coordinates

`kicad/drc.rpt` and numbers read from a saved board use KiCad's page coordinates, where the
board's top-left corner sits at (50, 50) mm. layout.py and the routing hooks count from the
corner. Subtract 50 from both x and y to get from a report to layout.py, and add 50 to go
the other way. (A report's `@(50.0000 mm, 50.0000 mm)` is the corner.)

## Freerouting

- On macOS its window opens while it routes, because there is no virtual display to hide
  it in. Leave it alone; pcbkit starts it, watches it and ends it.
- Nothing promises the same board for the same input. On one Mac a run once left a net
  unrouted and the next run completed it; on another, the same input gave the same route
  every try. That is why `route` makes several tries and stops at the first clean one. If
  every try ends with copper problems, more tries are rarely the answer: change the
  placement, or the hand routes and keep-outs in routing.py.
- A run that has not started routing within `stall_timeout_s` (90 s by default) is killed
  and counts as a failed try. With `--eco` the first stall makes `route` route the whole
  board instead, without using up a try.

## KiCad's own windows and files

- Open `kicad/<stem>.kicad_pcb` in KiCad to look. Do not save it and do not "update from
  schematic": the next build overwrites the file, and whatever you saved goes with it.
- pcbkit overwrites the files it makes and never deletes one it no longer makes (a library
  from before a rename, say). Delete `kicad/` and run `pcbkit build` when the folder has
  gone stale.
- It is built and checked on KiCad 10.0.x (10.0.6). `pcbkit doctor` says which version it
  found; on an older one expect differences.
