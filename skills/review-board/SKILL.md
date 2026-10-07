---
description: >-
  Review a routed pcbkit board's layout from pictures and the DRC report, and return only
  the findings: signals running under switching regulators, copper pours sliced into
  islands or thin necks, slivers, parts and tracks crowding the board edge. Use when the
  user asks for a layout review, a second look at the board, "does this layout look right",
  or a check before promote or ordering. Runs in a forked context because the renders,
  crops and DRC listings it reads are noisy.
context: fork
background: false
allowed-tools: Bash(pcbkit shots *) Bash(.venv/bin/pcbkit shots *) Read Glob Grep
---

Review the layout of the routed board in the pcbkit project in the current folder, and
report findings. Do not edit any file, and run no command other than `pcbkit shots`. Optional
focus from the user: $ARGUMENTS (region names, or a concern).

1. Run `pcbkit shots --no-render` from the project folder (`.venv/bin/pcbkit shots
   --no-render` if a bare `pcbkit` is not found). It writes an SVG and a PNG of each region
   into `out/shots/`. If it says there is no board, report that and stop.
2. Read `kicad/drc.rpt` if it exists and note anything other than 0 violations, 0
   unconnected pads and 0 footprint errors.
3. Look at every PNG in `out/shots/` with Read: `board_top` and `board_bottom` first, then
   the regions the project defines. Look for:
   - signal tracks under or right beside a switching regulator's inductor, diode or switch
     node, where its edges couple into them;
   - ground or power pours cut into islands or thin necks by tracks, so that part of a pour
     is no longer connected or carries its current through a narrow bridge;
   - slivers: thin spikes or slits of copper between a track and a pour edge;
   - anything else a layout reviewer would flag, marked as lower confidence.
4. Reply with findings only, most serious first, at most twelve. For each: where (layout
   millimetres: x, y from the board's top-left corner; a DRC position minus 50), what you saw,
   which picture shows it, why it matters, and which file would change it (layout.py for a
   position, routing.py for a hand route or keep-out). If you find nothing, say what you
   looked at. Do not paste image data or report listings into the reply.
