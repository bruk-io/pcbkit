# Troubleshooting

Start with `pcbkit doctor`: on a new machine most problems are a missing tool, and it names
the fix. The rest of this page goes by symptom: the message you see, why it happens, and what
to do. The examples use the blinky board, and paths in them are shortened.

| You see | Read |
|---|---|
| `MISSING` in the `doctor` report | [Reading pcbkit doctor](#reading-pcbkit-doctor) |
| `kicad-cli not found` | [KiCad or kicad-cli not found](#kicad-or-kicad-cli-not-found) |
| `pcbnew isn't importable here` | [pcbnew isn't importable here](#pcbnew-isnt-importable-here) |
| `pcbkit setup` fails, or `pytest isn't installed` | [When setup fails](#when-setup-fails) |
| `Freerouting jar not found`, `Java 17 or newer not found`, `stalled`, `timed out` | [Freerouting](#freerouting) |
| `gave up after N tries` | [A route that will not finish](#a-route-that-will-not-finish) |
| `DRC is not clean: nothing was exported` | [DRC errors after you edit the design](#drc-errors-after-you-edit-the-design) |
| `WARNING: ... is older than ...`, `STALE`, `no fab outputs` | [Stale fab outputs](#stale-fab-outputs) |
| `... is not defined in .../specs.py`, `missing-spec` | [A check fails on a missing spec](#a-check-fails-on-a-missing-spec) |
| Lines from KiCad that look like errors | [Noise from KiCad](#noise-from-kicad) |
| Where is the report? | [Where logs and reports live](#where-logs-and-reports-live) |

An error inside your own `design.py`, `layout.py`, `routing.py` or another module of yours
reaches you as an ordinary Python traceback through your file. Read the last frames first:

```
  File ".../blinky/design.py", line 27, in <module>
    C("C1", "100n", "VIN", "GND", B, "C0603", "CL10B104KB8NNNC", "Samsung")
NameError: name 'C' is not defined
```

That one is a missing `C` in the `from pcbkit.design import ...` line. Problems that pcbkit
finds itself (a mistyped key in `pcbkit.toml`, a hook of the wrong shape in `routing.py`) are
one-line messages that name the file and the key.

## Reading pcbkit doctor

```
$ .venv/bin/pcbkit doctor
OK       KiCad                   10.0.6  /Applications/KiCad/KiCad.app
OK       kicad-cli               10.0.6  /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
OK       KiCad libraries         /Applications/KiCad/KiCad.app/Contents/SharedSupport
OK       KiCad Python            Python 3.9.13, pcbnew 10.0.6  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
OK       Java                    17.0.20.1  .../bin/java
OK       Freerouting             1.9.0  ~/.local/share/freerouting/freerouting-1.9.0.jar
OK       rsvg-convert            2.60.0  /opt/homebrew/bin/rsvg-convert
OK       uv                      0.12.15  .../bin/uv
OK       ngspice (optional)      47  /opt/homebrew/bin/ngspice
OK       pcbnew here (optional)  importable: build, route, finalize and the other tier 2 commands run
All required items are present.
```

A header line above it names the pcbkit version, the operating system and the Python that is
running pcbkit. Then one line per item: `OK` or `MISSING`, the item, what was found (version
and path) or what is wrong, and for anything not `OK` a `(fix: ...)`. The last line counts
what is missing. The exit status is 1 if a required item is missing and 0 otherwise, so
`ngspice` and `pcbnew here`, which are optional, never fail it.

| Item | What it looks for | Fix |
|---|---|---|
| KiCad (macOS only) | `KiCad.app` at `/Applications/KiCad/`, version 10.0 or newer. | `brew install --cask kicad`, or the installer from kicad.org. |
| kicad-cli | On `PATH`, inside `KiCad.app`, or `/usr/bin`; 10.0 or newer. | The same. |
| KiCad libraries | KiCad's share folder with `footprints/`, `symbols/` and `3dmodels/`. `KICAD_SHARE` names another folder, and when it is set it is the only place looked at. | Reinstall KiCad, or set `KICAD_SHARE`. |
| KiCad Python | An interpreter that can `import pcbnew`, 10.0 or newer. On macOS only the one inside `KiCad.app`; elsewhere `python3` on `PATH` or `/usr/bin/python3`. | The same as KiCad. |
| Java | 17 or newer, from `JAVA_HOME`, `PATH`, or Homebrew's keg-only JDKs. | `brew install openjdk@21` (pcbkit finds that install itself); on Linux `sudo apt install openjdk-21-jre`. |
| Freerouting | `freerouting-1.9.0.jar` from `FREEROUTING_JAR`, else `~/.local/share/pcbkit/`, else `~/.local/share/freerouting/`. It must be a whole zip file. | `pcbkit setup` downloads it; or set `FREEROUTING_JAR`. |
| rsvg-convert | On `PATH`. `finalize` draws the assembly drawing with it, and `shots` every PNG. | `brew install librsvg`; on Linux `sudo apt install librsvg2-bin`. |
| uv | On `PATH`. | `brew install uv`; elsewhere the installer from astral.sh. |
| ngspice (optional) | On `PATH`. Only the checks that run SPICE need it. | `brew install ngspice`. |
| pcbnew here (optional) | Whether the Python running this pcbkit can import `pcbnew`. | `pcbkit setup`, then use the project's `.venv/bin/pcbkit`. |

Run from outside a project's `.venv`, with `uv run pcbkit doctor` for instance, the
`pcbnew here` line reads as below. That is normal, not a fault: "tier 2 commands" are the ones
that need `pcbnew` (see [pcbnew isn't importable here](#pcbnew-isnt-importable-here)).

```
MISSING  pcbnew here (optional)  not importable here, so tier 2 commands (build, route, ...) cannot run  (fix: run `pcbkit setup` in a board project, then run pcbkit from that project's .venv)
```

An item that a variable points at wrongly says so. With `FREEROUTING_JAR` set to a file that
is not there, the report ends:

```
MISSING  Freerouting             FREEROUTING_JAR=/nowhere/freerouting-1.9.0.jar does not exist  (fix: run `pcbkit setup` in a board project, or set FREEROUTING_JAR to the jar)
...
1 required item missing: Freerouting.
```

## KiCad or kicad-cli not found

```
kicad-cli not found: install KiCad 10 (brew install --cask kicad), or put its folder on PATH. `pcbkit doctor` shows what is missing.
```

pcbkit needs KiCad 10.0 or newer, and `pcbkit doctor` reports an older one with what it found,
as `found 9.0.2 at <path>; pcbkit needs 10.0`. Install KiCad 10 and run `pcbkit doctor` again.

- **macOS.** pcbkit looks for the app at `/Applications/KiCad/KiCad.app`, where the installer
  and `brew install --cask kicad` put it, and takes `kicad-cli`, KiCad's own Python and the
  libraries from inside it. Only `kicad-cli` is also looked for on `PATH`, and only the
  libraries can be pointed elsewhere (`KICAD_SHARE`): there is no setting for another KiCad's
  Python. A KiCad installed anywhere else is therefore only half found, so install it at the
  default place.
- **Linux.** pcbkit looks for `kicad-cli` on `PATH` and in `/usr/bin`, the libraries in
  `/usr/share/kicad`, and a `python3` (on `PATH`, or `/usr/bin/python3`) that imports
  `pcbnew`. `KICAD_SHARE` overrides the libraries.

## pcbnew isn't importable here

```
Error: pcbnew isn't importable here. In the board project, run: pcbkit setup
```

`build`, `route`, `promote`, `finalize`, `check`, `mutants` and `compare` drive KiCad's
`pcbnew` module, and it can only be imported by KiCad's own Python. A pcbkit that was started
from another Python (`uvx`, a `uv run` in the pcbkit checkout, your system Python) cannot import
it. The other commands (`new`, `doctor`, `setup`, `sch`, `quote`, `report` and `shots`) run
from any Python that has pcbkit.

In the board project, run `pcbkit setup` once. It builds the project's `.venv` on KiCad's
Python, so `pcbnew` imports there. Then run pcbkit from that `.venv`, either as
`.venv/bin/pcbkit build` or after `source .venv/bin/activate`. To see which one you are
running, `pcbkit doctor` names the Python in its first line (3.9.13 in the project's `.venv`,
which is KiCad's) and says on its `pcbnew here` line whether `pcbnew` imports.

## When setup fails

`pcbkit setup` says what failed and what to do, never a traceback. The messages, and what to
do about each, are in a table in the
[project interface](project-interface.md#pcbkit-setup). Two more you may meet:

**`uv sync failed` with "Repository not found".** A project made by `pcbkit new` depends on
pcbkit from its git repository (`pcbkit @ git+https://github.com/bruk-io/pcbkit` in its
`pyproject.toml`). If uv cannot read that repository (you are not signed in to GitHub, or it
has not been published yet), `setup` ends like this:

```
error: Failed to download and build `pcbkit @ git+https://github.com/bruk-io/pcbkit`
  cause: Git operation failed
  ...
         remote: Repository not found.
Error: `uv sync` failed (exit 1); its message is above. A dependency that cannot be found or built is the usual cause: check the network, and the dependencies and [tool.uv.sources] in pyproject.toml, then run `pcbkit setup` again.
```

Start the project again from your own copy of pcbkit, with `--pcbkit-source` (or set
`PCBKIT_SOURCE`). Run `new` from that copy, since the project does not exist yet:

```
uv run --project ~/src/pcbkit pcbkit new my-board --pcbkit-source ~/src/pcbkit
```

Or, in an existing project, replace the dependency with the bare name `pcbkit` and add the
source as the project interface describes under
[the project's pyproject.toml](project-interface.md#the-projects-pyprojecttoml), then run
`pcbkit setup` again.

**`pytest isn't installed in this environment`.** `pcbkit check` runs your checks with
pytest, and the project's `.venv` has none:

```
pytest isn't installed in this environment: the checks run on it. Add it to the project's dependencies (uv add pytest) and run again.
```

Put `pytest>=8` in `dependencies` in the project's `pyproject.toml` and run `pcbkit setup`.
That is the way to change dependencies: `setup` keeps `.venv` on KiCad's Python, and a bare
`uv sync` can rebuild it on another one (see the table linked above).

## Freerouting

The router is the Freerouting 1.9.0 jar, run with Java 17 or newer.

```
Freerouting jar not found: run `pcbkit setup`, or set FREEROUTING_JAR=/path/to/freerouting-1.9.0.jar
Java 17 or newer not found: install it (brew install openjdk@21); `pcbkit doctor` shows what is missing
```

`pcbkit setup` downloads the jar to `~/.local/share/pcbkit/` if it finds none, and checks its
size and that it is a zip file. A jar that is not whole is reported like this, and the fix is
what it says:

```
~/.local/share/pcbkit/freerouting-1.9.0.jar is not a complete jar (a truncated download?): delete it and run `pcbkit setup`
```

The search order, and what to do if the download fails, are in the project interface under
[the Freerouting jar](project-interface.md#the-freerouting-jar).

On macOS, Freerouting's window opens while it routes. Leave it alone: pcbkit starts it, watches
it and ends it.

### When it stalls

Freerouting sometimes hangs after it loads a board, before it routes anything. It prints
`Starting auto-routing` when it begins, so pcbkit waits for that line, and kills the run, and
everything it started, if it has not come within `stall_timeout_s` of `[route]` (90 seconds by
default). The run counts as a failed try. This is the output with `stall_timeout_s = 1`, set
to show it (see the third point):

```
$ .venv/bin/pcbkit route --tries 1
try 1/1: running Freerouting
try 1/1: stalled: no "Starting auto-routing" in 1 s: Freerouting stalled while loading the board
gave up after 1 tries: none of them made a routed board
```

When the router's log has a `normalization of net ... failed` line, which is how the hang
usually shows itself, the message ends with it: `(its log says "...")`. What to do:

1. **Run it again.** The hang does not always come back. `route` already makes `[route] tries`
   tries (three by default) and goes on to the next after a stall.
2. **Read `kicad/freerouting.log`.** If it stops after `Opening '<stem>.dsn'...`, with or
   without `normalization of net ... failed`, the router hung. On an `--eco` route a plain
   `pcbkit route` is the way out, and pcbkit already falls back to one (below). If a plain
   route hangs every time, send the log to whoever maintains pcbkit. If the log shows no
   failure at all, the machine may only have been slow to load it: raise `stall_timeout_s`.
3. **Do not set `stall_timeout_s` low.** The line comes about 20 seconds after launch even on
   a small board (23 seconds for blinky on the Mac this was written on), so a value under about
   30 seconds kills every run.

With `route --eco`, the first stall is not a failed try: `route` says so and routes the whole
board instead of keeping the old route, without using up a try.

```
the eco run stalled: no "Starting auto-routing" in 90 s: Freerouting stalled while loading the board
routing the whole board instead of keeping the old route
```

Two more ways a try can fail without making a board, each a `try` line of its own:

```
try 1/3: timed out: still running after 900 s
try 1/3: no result: Freerouting exited with code 1 without writing blinky.ses; the end of its log:
```

The first is a run still going after 900 seconds (15 minutes; that is not a setting). The
second is followed by the last lines of `kicad/freerouting.log`: read them. A line
`New version available: v2.5.0` in that log is Freerouting advertising itself and means
nothing.

## A route that will not finish

When no try comes out clean, `route` says so, leaves the best board in `kicad/` and exits 1.
This one had a keep-out strip across the board, put there on purpose, so the connector could
not reach the rest:

```
$ .venv/bin/pcbkit route --tries 2
try 1/2: running Freerouting
ses import True
zones filled, stitching vias: 30, dropped one-sided vias: 0
try 1/2: 2 copper problems (unconnected_items 2), 31 s
try 2/2: running Freerouting
ses import True
zones filled, stitching vias: 30, dropped one-sided vias: 0
try 2/2: 2 copper problems (unconnected_items 2), 31 s
gave up after 2 tries: the best was try 1: 2 copper problems (unconnected_items 2)
the best board is ~/blinky/kicad/blinky.kicad_pcb
silk       kicad/blinky.kicad_pcb: 4 text(s) added
DRC: 0 violations, 2 unconnected pads, 0 footprint errors
     2 unconnected_items
```

Each `try` line is one Freerouting run, finished and checked. A try is clean when none of
`unconnected_items`, `clearance`, `shorting_items`, `tracks_crossing`, `hole_clearance` and
`copper_edge_clearance` has an entry. Any other DRC category is not part of that test: `route`
can call a try clean and the `DRC:` line at the end still list something. `promote` accepts
only 0 violations, 0 unconnected pads and 0 footprint errors:

```
$ .venv/bin/pcbkit promote
Error: not promoting: blinky.kicad_pcb fails DRC
DRC: 0 violations, 2 unconnected pads, 0 footprint errors
     2 unconnected_items
golden/ holds only a route that passed with 0 violations, 0 unconnected pads and 0 footprint errors
```

The positions are in `kicad/drc.rpt`, in KiCad's file coordinates, which are your layout
coordinates plus 50 mm in both x and y:

```
[unconnected_items]: Missing connection between items
    Local override; error
    @(56.0000 mm, 61.2500 mm): PTH pad 1 [/VIN] of J1
    @(65.1750 mm, 61.2500 mm): Pad 1 [/VIN] of R1 on F.Cu
```

(56, 61.25) is (6, 11.25) in `layout.py`: J1. Read the category, then:

| Category | Usually means | First move |
|---|---|---|
| `unconnected_items` | The router could not finish a net, or a pre-route or keep-out walls it in. | Look at what stands between the two pads in `layout.py` and `routing.py`: a keep-out, a pre-route, a part. Give the net room. |
| `clearance`, `shorting_items`, `tracks_crossing`, `hole_clearance` | A hand pre-route or keep-out in `routing.py` crowds something, or two parts sit too close. | Change `routing.py` or `layout.py`. The router follows the rules; your hooks do not. |
| `copper_edge_clearance` | Something is too near the board edge. | Move the part, or make the board bigger. |

More tries rarely help. Freerouting does not always give the same board for the same input
(on one Mac it once left a net unrouted and finished it on the next run), which is why `route`
tries again, but when every try ends with the same problem, the board is the problem. Change
the placement, or the hand routes and keep-outs in `routing.py`. Recipes for those are in
[routing recipes](routing-recipes.md).

**Eco or full.** `pcbkit route --eco golden` keeps the route in `golden/` and only routes
what you changed: its copper is copied onto the new board and locked, except copper that
clashes with a change (dropped) and copper on the changed parts' nets or within
`[route] eco_unlock_reach_mm` of them (left free for Freerouting to rework). Use it for a
local change: a part nudged, a value changed, one net added. If an eco route will not come
clean, the frozen copper around the change may be boxing it in: raise `eco_unlock_reach_mm`,
or run a plain `pcbkit route`. A full route makes a new layout, so look at it again before
you promote it (`pcbkit shots`, and `pcbkit compare` to see what moved; both are in the
[project interface](project-interface.md#comparing-boards-and-taking-shots)).

If a keep-out of yours bans vias but not the pour, ground-stitching vias are still put inside
it, and DRC reports `items_not_allowed`. Ban the pour as well (it is the default of
`api.keepout`) for a region that must stay empty.

## DRC errors after you edit the design

DRC with schematic parity compares the board with the schematic. `pcbkit build` draws the
schematic again from `design.py`, but `pcbkit finalize` rebuilds the board from `golden/`, the
route of the last board you promoted. After you change `design.py` or `layout.py` the two
disagree until you route and promote again. Here a capacitor was added to `design.py`, and
`finalize` was run straight after `build`:

```
$ .venv/bin/pcbkit finalize
ses import True
zones filled, stitching vias: 26, dropped one-sided vias: 0
silk       kicad/blinky.kicad_pcb: 4 text(s) added
DRC: 0 violations, 0 unconnected pads, 1 footprint errors
     1 missing_footprint
Error: DRC is not clean: nothing was exported. Fix the board, or promote a route that passes.
```

`kicad/drc.rpt` names it: `[missing_footprint]: Missing footprint C1 (100n)`. A part whose
value you changed shows as `footprint_symbol_mismatch` instead. Bring `golden/` up to date:

```
.venv/bin/pcbkit build
.venv/bin/pcbkit route --eco golden     # a plain `route` after a big change
.venv/bin/pcbkit promote
.venv/bin/pcbkit finalize
```

Look at `DRC: 0 violations, 0 unconnected pads, 0 footprint errors` before you promote.

**`pcbkit check` right after `build`.** `build` makes the placed board with no copper and none
of the fields pcbkit copies onto each footprint (`MPN`, `Manufacturer`, `Description`) when it
finishes a board. The `kicad` check group then reports the unrouted connections as unconnected
pads, and `Missing symbol field 'MPN' in footprint` for each part. Do not add the fields by
hand: `route` and `finalize` write them. Run `check` on a finalized board.

A footprint error that is still there after you routed, promoted and finalized from an
up-to-date `golden/` means the board and the schematic disagree about a part. The board is
built from the netlist, so fix the part in `design.py` and run `build` again.

## Stale fab outputs

`finalize` rewrites `out/fab/` and `out/docs/` from the board as it is then. Anything you do
afterwards leaves those files describing an earlier board, and a `finalize` that stops at
`DRC is not clean: nothing was exported` leaves the earlier files exactly as they were. Three
things tell you.

**`pcbkit quote`** warns, and still prints the numbers, so read to the end:

```
WARNING: Blinky_revA_gerbers.zip is older than blinky.kicad_pcb: run `pcbkit finalize` before you rely on these numbers
WARNING: the Gerbers say 0.035, 0.035 mm copper but [stackup] copper_mm is 0.07: the files are from another board; run `pcbkit finalize`
```

**The `outputs` checks** compare the files with the board and the schematic. After a board
was rebuilt without a finalize, one of them fails like this:

```
AssertionError: PTH drill file {0.3: 26, 1.0: 2} vs board {1.0: 2}
```

With no export at all, each of them errors with:

```
Failed: no fab outputs under ~/blinky: run `pcbkit finalize`
```

**The order gate** of the Claude plugin, which you can also run yourself
([ordering](ordering.md#before-you-order)), prints one line per problem:

```
STALE: out/fab/Blinky_revA_gerbers.zip is older than design.py: run pcbkit build, route, promote and finalize
```

The cure is the same each time: `build`, `route` (or `route --eco golden`), `promote`,
`finalize`, as far back as your change reaches, then `pcbkit check`. Do not copy files into
`out/fab/` by hand, and do not order from files that `quote` or the order gate warns about.

## A check fails on a missing spec

A built-in check reads its numbers from your `specs.py` (and `circuits.py`). If a group is
switched on in `[checks] groups` and a number it needs is not there, the check fails and
names the name and the file. It does not skip, so a gap cannot pass for a green run:

```
specs.PINOUT is not defined in .../blinky/specs.py: add it (docs/project-interface.md says what each check group reads)
circuits.scenario is needed, but .../blinky/circuits.py does not exist: create it (docs/project-interface.md says what each check group reads)
```

A check that runs once per row of a table cannot run at all without the table, so it fails once
and is called `missing-spec` in its name, as in
`test_circuit::test_symbol_pin_functions_match_datasheet[missing-spec]`. Add the name, in the
type the [project interface](project-interface.md#built-in-groups-and-what-they-read) gives
for it, or switch the group off in `pcbkit.toml` if the board has nothing for it to judge.

Some cases that look alike are not errors, or are another error:

- A table that **exists but is empty** (`I2C_BUSES = {}` on a board with no bus) is not an
  error: its checks are skipped, and pytest says `got empty parameter set for (bus)`. A skip
  has its reason in `out/checks/results.json`.
- A name documented as optional may be left out.
- The `outputs` group needs the fab files, and errors until there are some, with "no fab
  outputs under ...: run `pcbkit finalize`" (see [Stale fab outputs](#stale-fab-outputs)).

Never loosen a check to make a gap go away: fill in the number, with its source beside it.
[Writing checks](writing-checks.md) says how.

## Noise from KiCad

On macOS, KiCad's `pcbnew` module writes lines that look like errors and are not. Without
pcbkit's filter you would see, once for the first board a process loads and then twelve for
every board after it:

```
./src/common/stdpbase.cpp(59): assert ""traits"" failed in Get(): create wxApp before calling this
08:22:43 AM: Debug: Adding duplicate image handler for 'PNG file'
```

pcbkit hides exactly those two patterns, only while it loads or makes a board, and shows every
other line. A KiCad or wxWidgets that words them differently makes them show again, by design:
pcbkit would rather show noise than hide a real error. If you see a line like that:

- Judge by the result, not the line. A command that exits 0 and prints its usual summary (the
  `DRC:` line, `ses import True`, `placed 3, missing: []`) worked.
- To get it hidden, tell whoever maintains pcbkit the line and the KiCad version from
  `pcbkit doctor`. The fix is one more pattern in `NOISE_PATTERNS` in `pcbkit/kicad/quiet.py`.
- A script of your own gets the raw noise, unless it gets `pcbnew` from
  `pcbkit.kicad.env.import_pcbnew()`.

`pcbkit check` also ends with a pytest warning that the SWIG-based Python interface to the PCB
editor is deprecated. That is KiCad announcing a future change to the interface pcbkit uses.
It does nothing today.

## Where logs and reports live

Everything is in the project folder, and everything in `kicad/` and `out/` is regenerated.

| File | What it is | Written by |
|---|---|---|
| `kicad/erc.rpt` | The ERC report of the schematic. | `sch`, `build` |
| `kicad/drc.rpt` | The latest DRC report, with positions in KiCad file coordinates. | `route`, `promote`, `finalize` |
| `kicad/freerouting.log` | The router's console output from its latest run. | `route` |
| `kicad/<stem>.kicad_pcb` | The finished board. Open it in KiCad to look; do not save it. | `route`, `finalize` |
| `out/checks/results.json` | Every check: outcome, skip reason or failure message, and the numbers it recorded. | `check` |
| `out/checks/VALIDATION.md` | That, as a report. | `report` |
| `out/fab/`, `out/docs/` | What you send, and what you read. | `finalize` |
| `out/shots/` | Crops and renders of regions of the board. | `shots` |

The `kicad` checks write their reports to a temporary folder and put the whole text in the
failure message, so for a failing `test_erc_clean` or `test_drc_clean_with_schematic_parity`,
read `message` in `results.json` or pytest's own output; `kicad/drc.rpt` is from the last
`route`, `promote` or `finalize` and may not be the same board.

## Asking for help

Send the whole output of the command that failed (not only its last line), the output of
`pcbkit doctor`, and whichever of `kicad/freerouting.log`, `kicad/drc.rpt` and
`out/checks/results.json` applies. For a routing problem, say whether the same board routed
before your last change, and what that change was.
