---
description: >-
  Start a new board with pcbkit: interview the user for what the board must do, scaffold a
  project with `pcbkit new`, set it up, fill in design.py, layout.py and specs.py, and get
  the first build clean. Use when the user wants to design, create or start a new PCB or
  board project, turn a circuit idea into a KiCad board, or scaffold a pcbkit project, and
  there is no pcbkit.toml for it yet. Not for working an existing project (board-workflow).
---

# Starting a new board

The result of this skill is a project that builds with no ERC errors and no part left
unplaced. Routing, checks and ordering come after, in board-workflow.

## 1. Interview first

Ask once, in one batch, only what changes the design. Skip what a datasheet or the user's
message already answers.

- What the board does, what feeds it (voltage range, largest continuous current on any
  path) and what plugs into it. The current decides track widths and copper weight; the
  hazards worth guarding (reverse polarity, hot-plugging a battery, a 5 V signal into a
  3.3 V pin) become checks later.
- Size limits, mounting holes, and which edge each connector must sit on.
- Hand-soldered, or assembled by the fab. Assembled parts must be ones the fab stocks.
- Any part the user has already chosen, or a module the board hosts.

pcbkit builds two-layer boards only. Say so if the user expects four.

## 2. Scaffold and set up

Check the machine first with `pcbkit doctor`, and fix what it reports before going on. Then
`pcbkit new my-board` (before pcbkit is installed:
`uvx --from git+https://github.com/bruk-io/pcbkit pcbkit new my-board`), `cd my-board`,
`pcbkit setup`. The new project is a small working example, one LED on a connector, with
two checks and a planted mistake. If either command says "not implemented yet", the pcbkit
in use is older than this skill: tell the user, and do not build the folder by hand.

## 3. Fill it in

- design.py: one block per function of the board. Every part gets a real, orderable part
  number; an unorderable one is reported by `finalize` later, so settle them now.
- Never guess a pin number, a footprint or a rating. Dispatch the `parts-researcher` agent
  for each part that needs a datasheet number or a stock check, in parallel, and write the
  figure and its source beside it in specs.py.
- layout.py: board size, then positions. Connectors on the edges the user named,
  decoupling capacitors next to the pins they serve, a mounting hole where the user wants a
  screw. Give every part a position.
- pcbkit.toml: `copper_mm` for the current you were told (0.035 is 1 oz, 0.070 is 2 oz),
  and the check groups to switch on.

## 4. First build

`pcbkit build` until ERC reports nothing and `missing` is empty. Read what ERC says before
changing anything: an unconnected pin is a decision, not noise. Then hand over to
board-workflow for `route`, and to add-check for the hazards from the interview.
