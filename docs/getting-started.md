# Getting started

Install pcbkit, build the example board all the way to fab files, then change the board and
build it again. The commands take a few minutes in all; the first `setup` also downloads
Python packages and Freerouting.

The outputs below come from real runs on a Mac. Paths (`/Users/you`), versions and timings
will differ on your machine, and `~/src/pcbkit` stands for wherever your copy of pcbkit is.

## What you need

- A Mac with [Homebrew](https://brew.sh). pcbkit is built and tested on macOS; `pcbkit
  doctor` has install hints for Linux, but Linux has not been tried.
- KiCad 10 or newer, Java 17 or newer (Freerouting runs on it), ngspice (only checks that
  run SPICE need it), librsvg (its `rsvg-convert` draws the PNGs) and
  [uv](https://docs.astral.sh/uv/).

From a checkout of pcbkit, one command installs all five:

```
brew bundle --file=Brewfile
```

Without a checkout, `brew install --cask kicad`, then `brew install openjdk@21 ngspice
librsvg uv`.

## Get pcbkit

`pcbkit new` and `pcbkit setup` run before your board has a project environment, so they
need a pcbkit that lives somewhere else ([concepts](concepts.md#the-two-tiers) says why).
Install it once as a command:

```
uv tool install --from ~/src/pcbkit pcbkit
```

That is for a checkout, which is what you have until the repository is public. After that,
`uv tool install git+https://github.com/bruk-io/pcbkit` does the same from GitHub. If your
shell then says `pcbkit: command not found`, run `uv tool update-shell` and open a new
terminal.

Prefer not to install anything? Write `uv run --project ~/src/pcbkit pcbkit` (a checkout)
or `uvx --from git+https://github.com/bruk-io/pcbkit pcbkit` (GitHub) wherever this page
says `pcbkit new`, `pcbkit setup` or the first `pcbkit doctor`.

## Make your first board

### Check the machine

```
pcbkit doctor
```

On a machine that has the tools but has not run `setup` yet, it prints:

```
pcbkit 0.1.0: macos, Python 3.14.0
OK       KiCad                   10.0.6  /Applications/KiCad/KiCad.app
OK       kicad-cli               10.0.6  /opt/homebrew/bin/kicad-cli
OK       KiCad libraries         /Applications/KiCad/KiCad.app/Contents/SharedSupport
OK       KiCad Python            Python 3.9.13, pcbnew 10.0.6  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
OK       Java                    21.0.11  /opt/homebrew/opt/openjdk@21/bin/java
MISSING  Freerouting             freerouting-1.9.0.jar is not in ~/.local/share/pcbkit or ~/.local/share/freerouting  (fix: run `pcbkit setup` in a board project, or set FREEROUTING_JAR to the jar)
OK       rsvg-convert            2.60.0  /opt/homebrew/bin/rsvg-convert
OK       uv                      0.9.2  /opt/homebrew/bin/uv
OK       ngspice (optional)      47  /opt/homebrew/bin/ngspice
MISSING  pcbnew here (optional)  not importable here, so tier 2 commands (build, route, ...) cannot run  (fix: run `pcbkit setup` in a board project, then run pcbkit from that project's .venv)
1 required item missing: Freerouting. Optional, not found: pcbnew here.
```

Both `MISSING` lines are expected at this point. `setup` fetches Freerouting, and "pcbnew
here" is what the second tier of commands needs: `setup` makes the place where it works.
Anything else that is missing comes with a fix: apply it, then run `pcbkit doctor` again.

### Make the project

From the folder where you keep your boards:

```
pcbkit new my-board --pcbkit-source ~/src/pcbkit
cd my-board
```

```
Made My Board in my-board/, from the blinky example (14 files).
pcbkit comes from /Users/you/src/pcbkit.

Next:
  cd my-board
  pcbkit setup
  .venv/bin/pcbkit build

README.md has the rest of the commands.
```

`new` copies the blinky example into `my-board/` and names the board after the folder: the
KiCad file stem is `my_board`, the title is `My Board`, and the fab files start with
`My_Board_revA`. `--pcbkit-source` makes the project install pcbkit from your checkout;
once the repository is public you can leave it off and the project installs pcbkit from
GitHub.

What you now have is a small, working project, 30 x 20 mm: a two-pin connector, a 330 ohm
resistor and a green LED. [Concepts](concepts.md#a-board-is-a-project) says what each file
is for. `golden/` already holds a route for it, so the board builds as it is.

### Set it up

```
pcbkit setup
```

```
pcbkit setup: My Board in /Users/you/boards/my-board
uv  0.9.2  /opt/homebrew/bin/uv
KiCad Python  3.9.13, pcbnew 10.0.6  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
.venv  making it on KiCad's Python (uv venv --system-site-packages)
.venv  installing the dependencies in pyproject.toml (uv sync)
...
 + numpy==2.0.2
 + pcbkit==0.1.0 (from file:///Users/you/src/pcbkit)
 + scipy==1.13.1
...
.venv  imports pcbnew 10.0.6
Freerouting  downloading 1.9.0 (5.0 MB) to /Users/you/.local/share/pcbkit/freerouting-1.9.0.jar
Java  21.0.11  /opt/homebrew/opt/openjdk@21/bin/java

Ready. Next:
  .venv/bin/pcbkit doctor
  .venv/bin/pcbkit build
  .venv/bin/pcbkit route
  .venv/bin/pcbkit promote
  .venv/bin/pcbkit finalize
  .venv/bin/pcbkit check
```

(The `...` lines stand for uv's progress and package list.) `setup` made `.venv` on KiCad's
own Python, installed the project's dependencies into it, and proved that `import pcbnew`
works there. That is the line to look for. It also downloaded Freerouting, once, into
`~/.local/share/pcbkit/`.

From here on you run `.venv/bin/pcbkit`. You never need to activate the environment. Its
`doctor` now finishes with:

```
OK       pcbnew here (optional)  importable: build, route, finalize and the other tier 2 commands run
All required items are present.
```

### Build

```
.venv/bin/pcbkit build
```

```
ERC        0 errors, 0 warnings (kicad/erc.rpt)
placed 3, missing: []
```

`build` drew the schematic from `design.py`, ran KiCad's ERC on it, exported the netlist,
and placed the board from that netlist at the positions in `layout.py`. `placed 3, missing:
[]` says three footprints were placed and no part lacked a position.

### Route

```
.venv/bin/pcbkit route
```

```
try 1/3: running Freerouting
ses import True
zones filled, stitching vias: 26, dropped one-sided vias: 0
try 1/3: clean, 38 s
silk       kicad/my_board.kicad_pcb: 4 text(s) added
DRC: 0 violations, 0 unconnected pads, 0 footprint errors
```

This takes about 40 seconds. On a Mac a Freerouting window opens while it runs; leave it
alone, pcbkit starts it, watches it and closes it. After the router, pcbkit imported the
route, filled the ground pours on both layers, added 26 stitching vias between them, added
the silkscreen text, and ran KiCad's DRC with schematic parity. `try 1/3: clean` means the
first router run needed no second attempt. If a run leaves copper problems, `route` tries
again, up to three times, and keeps the best board.

### Promote and finalize

```
.venv/bin/pcbkit promote
.venv/bin/pcbkit finalize
```

`promote` prints one line, and `finalize` prints five:

```
promoted to golden/: prerouted.kicad_pcb, my_board.ses, my_board.dsn
```

```
ses import True
zones filled, stitching vias: 26, dropped one-sided vias: 0
silk       kicad/my_board.kicad_pcb: 4 text(s) added
DRC: 0 violations, 0 unconnected pads, 0 footprint errors
BOM lines: 3 total parts: 3
```

`promote` checked the route with DRC once more and saved it into `golden/`: that is the
route you have accepted. `finalize` then rebuilt the board from `golden/` without running
the router, checked it again, and wrote the fab files. It takes about 20 seconds, most of
it the 3D renders (`finalize --no-render` skips them). If DRC finds anything, `finalize`
stops and exports nothing.

### Check, and plant mistakes

```
.venv/bin/pcbkit check
```

```
...........                                                              [100%]
=============================== warnings summary ===============================
test_outputs.py::test_drill_files_match_board
  .../pcbnew.py:63: DeprecationWarning: The SWIG-based Python interface to the PCB editor is deprecated and will be removed in a future version of KiCad. ...

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
11 passed, 1 warning in 3.71s
Results: out/checks/results.json (pcbkit report)
```

Eleven checks passed: KiCad's ERC and DRC; seven that read the Gerbers, drill files, BOM
and pick-and-place file back and compare them with the board and the schematic; and the
two in `checks/test_indicator.py`, which are this board's own: the LED's current stays
between 1 and 20 mA across the 3 V to 5.5 V supply, and R1 stays under half its power
rating. The warning is KiCad's own: the Python interface that pcbkit uses is deprecated
and will be removed in a future KiCad. It does not affect the run.

A check that passes proves little until you have seen it fail:

```
.venv/bin/pcbkit mutants
```

```
control (no edits)                                 PASSES  2 passed, 9 deselected in 0.73s
R1 is 10 ohm: the LED is overdriven                CAUGHT  (1 failed, 10 deselected in 0.81s)
R1 is 10 ohm: the resistor overheats               CAUGHT  (1 failed, 10 deselected in 0.76s)
D1 is the wrong way round: the LED never lights    CAUGHT  (1 failed, 10 deselected in 0.75s)

3/3 planted mistakes caught
```

Each line after the control is a mistake from `mutants.py`, planted in a scratch copy of
`design.py`, and it is caught when a check fails. [Concepts](concepts.md#mutants) has the
idea behind this.

## Look at what you made

| Where | What |
|---|---|
| `kicad/my_board.kicad_sch` and `kicad/my_board.kicad_pcb` | The schematic and the finished board. |
| `kicad/erc.rpt` and `kicad/drc.rpt` | The ERC and DRC reports. |
| `out/fab/My_Board_revA_gerbers.zip` | The Gerbers and drill files: the file you upload to a fab house. |
| `out/fab/My_Board_revA_BOM.csv` and `.xlsx`, `My_Board_revA_centroid.csv` | The bill of materials and the pick-and-place file. |
| `out/docs/` | The schematic, assembly and copper drawings as PDFs, and 3D renders. The picture on the [home page](index.md) was made from `My_Board_revA_render_iso.png`. |
| `out/checks/results.json` | What each check did. `pcbkit report` turns it into `out/checks/VALIDATION.md`. |

To look at the board in KiCad, on a Mac:

```
open kicad/my_board.kicad_pcb
open kicad/my_board.kicad_sch
```

Look, but do not save, and do not run "Update PCB from Schematic": the next `pcbkit build`
overwrites the file and your change goes with it. Change the Python instead. The schematic
has a net label on every pin and no wires, because pcbkit joins pins by net name.

## Change something

### Move a part

In `layout.py`, move D1 two millimetres to the right:

```python
P = {
    "J1": (6.0, 11.25, 90),
    "R1": (16.0, 11.25, 0),
    "D1": (25.0, 11.25, 180),  # was 23.0
}
```

A change to the layout or the circuit goes through the same four commands again. The
difference is `--eco golden`, which keeps the route in `golden/` and routes only what the
change touched. Use a plain `route` when you rearrange a lot.

```
.venv/bin/pcbkit build
.venv/bin/pcbkit route --eco golden
.venv/bin/pcbkit promote
.venv/bin/pcbkit finalize
```

`route --eco golden` prints:

```
changed footprints: ['D1'] new pre-route pieces: 0
kept 2 routed pieces from golden (1 left unlocked near the change), dropped 0 that clash with it
try 1/3: running Freerouting
ses import True
zones filled, stitching vias: 25, dropped one-sided vias: 0
try 1/3: clean, 31 s
silk       kicad/my_board.kicad_pcb: 4 text(s) added
DRC: 0 violations, 0 unconnected pads, 0 footprint errors
```

The second line says what `--eco` did: it copied two routed pieces from `golden/` onto the
new board, and left one of them unlocked, next to D1, so that the router may rework it.

### Change the resistor

In `design.py`, try 1 kilohm in place of the 330 ohm. Change the part number with the
value: pcbkit's checks cannot tell that `RC0603FR-07330RL` means 330 ohm.

```python
R("R1", "1k", "VIN", "LED_A", B, "RC0603FR-071KL", "Yageo")
```

The checks read the netlist, so you do not need to route to find out whether this works.
Build, then run just the LED check:

```
.venv/bin/pcbkit build
.venv/bin/pcbkit check -k test_led_current_window
```

```
E       AssertionError: LED current (mA) outside 1 to 20 mA: {'D1 dimmest': 0.5}
...
FAILED checks/test_indicator.py::test_led_current_window - AssertionError: LE...
1 failed, 10 deselected in 0.56s
```

At the lowest supply the board allows, 3 V, the LED's worst-case forward voltage is 2.5 V,
which leaves 0.5 mA across 1 kilohm: dimmer than the 1 mA that `specs.py` counts as lit.
A 300 ohm resistor is slightly brighter than the 330 and stays within both limits. (`check
-k` leaves a partial result in `out/checks/results.json`, so run `pcbkit check` with no
`-k` before you order boards.)

```python
R("R1", "300", "VIN", "LED_A", B, "RC0603FR-07300RL", "Yageo")
```

```
.venv/bin/pcbkit build
.venv/bin/pcbkit route --eco golden
.venv/bin/pcbkit promote
.venv/bin/pcbkit finalize
.venv/bin/pcbkit check
```

All eleven checks pass again, and the BOM in `out/fab/My_Board_revA_BOM.csv` now lists
`RC0603FR-07300RL`.

### If you skip a step

Change the value, run `build`, and go straight to `finalize`:

```
...
DRC: 0 violations, 0 unconnected pads, 1 footprint errors
     1 footprint_symbol_mismatch
Error: DRC is not clean: nothing was exported. Fix the board, or promote a route that passes.
```

`finalize` rebuilds from `golden/`, which still holds the board as it was when you promoted
it, with the old value on R1. `kicad/drc.rpt` names the part and the field. The way out is
the loop above: `route --eco golden`, `promote`, `finalize`.

A refused `finalize` exports nothing, and it leaves the last good export where it was, so
`out/fab/` still describes the old board. `pcbkit check` notices: `test_bom_matches_schematic`
fails because the BOM still has the old part number, and so does the DRC check. Do not
upload anything from `out/` until `finalize` has succeeded and `check` passes.

## Where next

- [Concepts](concepts.md): what each file is for, how the pipeline fits together, and why
  golden routes and mutants exist.
- [The board project interface](project-interface.md): every key and hook.
- [Routing recipes](routing-recipes.md) and [writing checks](writing-checks.md), for the
  two things you will change most on a real board.
- [Ordering](ordering.md): `pcbkit quote` prints the numbers PCBWay's form asks for.
- [The Claude plugin](claude-plugin.md), if you work in Claude Code: it can start a board
  and run this loop for you, with guard hooks that keep edits out of the generated folders.
- [Troubleshooting](troubleshooting.md), when a command does not do what this page said.
