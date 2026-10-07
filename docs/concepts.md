# Concepts

pcbkit is a pipeline of small stages that hand files to each other. The
[board project interface](project-interface.md) is the reference for every file and key;
this page explains how the pieces fit, and links there for the details.

## A board is a project

A board project is a folder with a `pcbkit.toml`. Every command finds it from the current
folder upwards. These are the files you write:

| File | What it says | Reference |
|---|---|---|
| `pcbkit.toml` | The board's names and revision, the stackup, the router and stitching settings, the fab profile, and which check groups run. | [pcbkit.toml](project-interface.md#pcbkittoml) |
| `design.py` | The circuit: every part, and the net on each of its pins. | [design.py](project-interface.md#designpy) |
| `layout.py` | The board's size, corners and mounting holes, and where each part sits. | [layout.py](project-interface.md#layoutpy) |
| `routing.py` | What routing needs from you: net classes, design rules, hand-made routes, keep-outs and the ground pours. | [routing.py](project-interface.md#routingpy) |
| `silk.py` | The silkscreen text: labels, polarity marks, the title block. | [silk.py](project-interface.md#silkpy) |
| `specs.py` | The numbers the checks read, such as datasheet limits. | [Checks](project-interface.md#checks-specspy-circuitspy-checks-and-mutantspy) |
| `checks/test_*.py` | Your own checks, written as pytest functions. | [Writing a check](project-interface.md#writing-a-check) |
| `mutants.py` | Planted mistakes that the checks must catch. | [mutants.py](project-interface.md#mutantspy) |

Three more are optional: `bom.py` (when the BOM should say something other than the
schematic), `footprints.py` and `footprints/` (a symbol or footprint that KiCad does not
have), and `circuits.py` (operating-point and SPICE helpers for the circuit checks).

In `design.py`, two pins that name the same net are connected: pcbkit draws the schematic
with a label on each pin and no wires. The board is built from the netlist that KiCad
exports from that schematic, not from `design.py` directly, so the two cannot drift apart.
Coordinates in `layout.py` and in the routing hooks are millimetres from the board's
top-left corner, with y pointing down; pcbkit adds KiCad's page offset itself.

## The pipeline

Eight stages take the project from circuit to fab files. The second column says which
command runs each one, and the last what you steer it with.

| Stage | Run by | What happens | You steer it with |
|---|---|---|---|
| 1. Schematic and ERC | `sch`, `build` | Draws the schematic from `design.py`, runs KiCad's ERC on it and exports the netlist. | `design.py` |
| 2. Placement | `build` | Builds the board from the netlist: each footprint at its position, the outline, the mounting holes and the stackup. | `layout.py`, `[stackup]` |
| 3. Pre-route | `route` | Applies the net classes and design rules, runs your `prerouted` and `keepouts` hooks (hand-made copper is locked, so the router goes round it), adds a keep-out strip along each board edge and exports the router's input. | `routing.py` |
| 4. Freerouting | `route` | The autorouter completes what is left. It runs up to `[route] tries` times, and a run that never starts routing is killed after `stall_timeout_s`. | `[route]` |
| 5. Post | `route`, `finalize` | Imports the router's result, runs your `gnd_links` and `zones` hooks, fills the pours, stitches the ground layers together with vias, and takes out vias that carry copper on one layer only and tracks that end nowhere. | `routing.py`, `[stitch]` |
| 6. Silkscreen | `route`, `finalize` | Adds your labels, a reference designator for every part, the assembly drawing text and the title block. | `silk.py` |
| 7. DRC | `route`, `promote`, `finalize` | KiCad's design rule check, with schematic parity: the board must match the schematic as well as obey the rules. | the rules in `routing.py` |
| 8. Fab files | `finalize` | The Gerbers and drill files in a zip, the BOM, the pick-and-place file, PDFs and 3D renders. | `bom.py`, `[fab]` |

```text
build      design.py -> [1 schematic, ERC, netlist] -> [2 placement]
route      [3 pre-route] -> [4 router] -> [5 post] -> [6 silk] -> [7 DRC]
promote    the routed board, if its DRC is clean -> golden/
finalize   golden/ -> [5 post] -> [6 silk] -> [7 DRC] -> [8 fab files]
```

`route` runs stage 3, then stages 4 and 5 with a DRC after each try, until a try comes out
with no copper problem; it finishes with stages 6 and 7. `finalize` starts from `golden/`
instead of the router: it runs stages 5 to 8, and stops without exporting anything if DRC
finds a single problem.
[The commands that run these hooks](project-interface.md#the-commands-that-run-these-hooks)
has the details of both, and the files each stage hands to the next.

## Golden routes

Freerouting does not promise the same board for the same input. On one Mac a run once left
a net unrouted and the next run completed it. So a fresh route is a new layout, and you
should look at it before you order boards made from it.

`pcbkit promote` is how you say "this one". It runs DRC on the routed board, refuses unless
there is nothing to report, and copies three files into `golden/`: the placed board with
your hand-made routes (`prerouted.kicad_pcb`), the router's result (`<stem>.ses`) and its
input (`<stem>.dsn`). `pcbkit finalize` rebuilds the board from those, with no router, so
the fab files always come from a route you accepted. `golden/` is part of your project:
commit it.

When you change the circuit or the layout, you do not have to lose that route. `pcbkit
route --eco golden` copies the golden route's copper onto the new board and locks it,
except where it clashes with what changed, and lets Freerouting complete the rest. If the
router stalls, `route` routes the whole board instead and says so. Use a plain `route` when
the placement really changed: it is a new layout, so review it again. Either way, `promote`
and `finalize` follow.

`finalize` runs DRC with schematic parity on what it rebuilt, so a golden route that has
fallen out of step with the schematic, after a part's value changed for instance, stops it
before anything is exported (see
[Getting started](getting-started.md#if-you-skip-a-step)). To see what a re-route moved,
keep a copy of the old `kicad/<stem>.kicad_pcb` and run `pcbkit compare OLD NEW`
([comparing boards](project-interface.md#comparing-boards-and-taking-shots)).

## Checks, specs and mutants

`pcbkit check` runs two kinds of check with pytest, in one run: the built-in checks of the
groups listed in `[checks] groups`, and your own, the `test_*.py` files in `checks/`.

| Group | What it judges | Reads |
|---|---|---|
| `kicad` (on by default) | ERC on the schematic, and DRC with schematic parity. | nothing |
| `outputs` (on by default) | The Gerbers, drill files, BOM and pick-and-place file, read back and compared with the board and the schematic; and that every fitted part has a part number a buyer can order. | the fab files, `pcbkit.toml`, `layout.py` |
| `fab` | The fab house's limits, polarity marks on wire pads, decoupling capacitors close to their pins, the rise time of an I2C bus. | `specs.py` |
| `copper` | Regions reserved for some nets, ground stitching, coupling into sensitive traces, the current and voltage drop of the heavy copper. | `specs.py` |
| `circuit` | Operating points solved from the netlist: pins against the datasheet, LED currents, resistor power, capacitor voltage, supply pins. | `specs.py`, `circuits.py` |
| `esp32s3` | Pin rules for a board that carries an ESP32-S3 DevKit: strapping pins, analogue inputs, the current each GPIO sources. | `specs.py` |

What each group reads, name by name, is in
[Built-in groups](project-interface.md#built-in-groups-and-what-they-read).

`specs.py` is where the numbers live: a datasheet limit, a design range, the fab house's
minimums. Write each one's source in a comment next to it, as blinky's `specs.py` does. A
group that you switch on but give no number to does not skip: its check fails and names
the missing name and the file, so a check cannot go quiet because nobody wrote down what
it should hold the board to.

Your own checks are ordinary pytest functions that ask for fixtures: `nl` (the netlist),
`board` (the routed board), `specs`, `record`. The blinky example has two, in
`checks/test_indicator.py`
([source](https://github.com/bruk-io/pcbkit/blob/main/examples/blinky/checks/test_indicator.py)).
[Writing checks](writing-checks.md) shows how to write your own.

A run leaves `out/checks/results.json`, and `pcbkit report` turns it into
`out/checks/VALIDATION.md`. A run with `-k` leaves a partial result, marked as such, which
the order skill in the [Claude plugin](claude-plugin.md) refuses.

### Mutants

A check that has never been seen to fail is not trusted. A check that is always green may
be checking nothing: a net name with a typo, a limit that is far too loose, a table that
came out empty. So every hazard gets a planted mistake, and you watch the check fail on it.

`mutants.py` lists mistakes as edits to `design.py`: what is wrong, the text to find and
its replacement, and the check that must then fail. `pcbkit mutants` first runs a control,
the same checks on an unedited scratch copy of the project, which must pass. Then it plants
each mistake in a fresh scratch copy, rebuilds the schematic there and runs the checks. A
mistake is **caught** when a check fails, and **missed** when every check still passes, or
when the text to find is not in `design.py`: a stale entry counts as a miss. The exit code
is 0 when every mistake is caught, 1 when one is missed and 2 when the control fails.

Mutants edit `design.py` only, so they prove the checks that read the netlist. A check on
the copper or the layout is proven another way: a test that builds a bad board and asserts
that the check fails on it.

A failing check is information. Never loosen a limit, skip a check or delete it to get a
green run: fix the design, or change the limit and write down the source for the new
number.

## The two tiers

KiCad's `pcbnew` Python module is how pcbkit reads and edits a board: it moves tracks,
fills pours and runs the design rules. It only imports under the Python that ships inside
KiCad (3.9 in KiCad 10 on macOS), so it cannot be imported from whichever Python you
installed pcbkit with. That is why pcbkit runs on Python 3.9 and newer, and why its
commands come in two tiers:

| | Tier 1 | Tier 2 |
|---|---|---|
| Commands | `new`, `doctor`, `setup`, `sch`, `report`, `quote`, `shots` | `build`, `route`, `promote`, `finalize`, `check`, `mutants`, `compare` |
| Imports `pcbnew` | no | yes |
| Runs in | any Python that has pcbkit: `uvx`, `uv tool install`, a virtual environment | the board project's own `.venv`, as `.venv/bin/pcbkit` |
| Also needs | `sch` and `shots` call `kicad-cli`; `shots` also needs `rsvg-convert` | `kicad-cli`, and `rsvg-convert` for `finalize`; `route` also needs Java and Freerouting |

`pcbkit setup` joins the two. It builds `.venv` on KiCad's own Python with
`--system-site-packages`, installs the project's dependencies (pcbkit and pytest) into it,
and checks that `import pcbnew` works there. From then on you run tier 2 commands as
`.venv/bin/pcbkit`; you never activate the environment. Run one anywhere else and it stops
at once, with a message instead of a traceback:

```
Error: pcbnew isn't importable here. In the board project, run: pcbkit setup
```

If you change the project's dependencies, run `pcbkit setup` again rather than a bare `uv
sync`, which can rebuild `.venv` on a Python that cannot import `pcbnew`.

## Generated folders

pcbkit writes three folders and treats a fourth as generated too. Never edit them by hand:
the next command that writes them overwrites your edit, and so does a save from KiCad's GUI.
Change the Python instead.

| Folder | What is in it | Written by | In git |
|---|---|---|---|
| `kicad/` | The schematic, netlist, placed and routed boards, the ERC and DRC reports, the router's files. | `sch`, `build`, `route`, `promote`, `finalize` | No: `.gitignore` leaves it out. |
| `out/` | `out/fab/` (what you send to the fab house), `out/docs/` (PDFs and renders), `out/checks/`, `out/shots/`. | `finalize`, `check`, `report`, `shots` | No. |
| `golden/` | The route you accepted. | `promote` | Yes: `finalize` needs it. |
| `fab/` | Not written by any pcbkit command. If it exists, `quote` and the `outputs` checks read it when `out/fab/` is not there. | you | Your choice. |

pcbkit never deletes a file that it no longer makes. When `kicad/` has gone stale, delete
the folder and run `pcbkit build`: `golden/` keeps the route. Opening the files in KiCad to
look at them is fine. The guard hooks of the [Claude plugin](claude-plugin.md) stop Claude
Code from editing these folders, though they cannot see a shell command that does.
