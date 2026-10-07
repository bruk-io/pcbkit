# Ordering boards

pcbkit does not place orders. It writes the files a fab house and an assembler take, and
`pcbkit quote` prints every number the PCBWay order form asks for, so you never count parts
by hand. PCBWay is the only fab profile so far (`[fab] profile = "pcbway"`). You fill in the
form yourself, or you let the Claude plugin's [order skill](#ordering-with-claude) fill it in
and stop before anything that costs money.

The examples use the blinky board (`pcbkit new blinky`) and run from its folder with its own
`.venv`. `quote` needs no pcbnew, so it also runs from any Python that has pcbkit.

## What finalize writes

`pcbkit finalize` rebuilds the board from `golden/`, runs DRC and, only if DRC is clean,
exports the files below. `<fab_name>` is `fab_name` in `[board]` of `pcbkit.toml`, so blinky
writes `Blinky_revA_...`.

```
$ .venv/bin/pcbkit finalize
ses import True
zones filled, stitching vias: 26, dropped one-sided vias: 0
silk       kicad/blinky.kicad_pcb: 4 text(s) added
DRC: 0 violations, 0 unconnected pads, 0 footprint errors
BOM lines: 3 total parts: 3
```

| File | What it is | What you do with it |
|---|---|---|
| `out/fab/<fab_name>_gerbers.zip` | The Gerber and drill files in one zip. | Upload it for the bare board. Upload the zip, not the loose files in `out/fab/gerbers/`. |
| `out/fab/<fab_name>_BOM.csv` and `_BOM.xlsx` | The bill of materials, with the same rows in both. The workbook also carries your "not assembled" notes (see `bom.py` below). | Upload one of them for assembly. |
| `out/fab/<fab_name>_centroid.csv` | Where each BOM part sits, for the pick-and-place machine: top side only, x to the right and y up from the board's bottom-left corner. | Upload it for assembly. |
| `out/docs/` | The schematic and the two copper layers as PDFs, the assembly drawing (PDF and PNG) and three 3D renders. | Read them before you order, and send them if the assembler asks a question. `finalize --no-render` leaves the renders out. |

`finalize` empties `out/fab/` and `out/docs/` before it writes, so a file from an earlier
export cannot end up in the zip. The full list, and how the centroid is measured, is in the
[project interface](project-interface.md#fab-outputs).

If DRC finds anything, `finalize` stops with `DRC is not clean: nothing was exported` and
leaves the previous `out/fab/` as it was. Those files are then out of date: do not order from
them.

### Part numbers

The last lines of `finalize` list every fitted part a buyer could not order: no manufacturer
part number, the stand-in part number that `R()` makes up for a resistor, or a "part number"
with a space in it (that is a description). `finalize` still succeeds:

```
BOM lines: 3 total parts: 3
  warning: no orderable part number: R1 (332): '0603 332 1%' is the stand-in part number pcbkit.design.R makes up: give the part an mpn, or its value a default resistor
```

The `outputs` check `test_bom_complete` fails for the same parts, so `pcbkit check` will not let
them through. The fix is to give the part an `mpn=` (and `mfr=`) in `design.py`. A resistor
whose value is in pcbkit's default table of Yageo 0603 parts gets one without being told: see
[what the BOM lists](project-interface.md#what-the-bom-lists).

### bom.py: when the BOM should say something else

Most boards need no `bom.py`. It is for when what the schematic says is not what you buy: a
reference that stands for two physical parts, a generic part number that is bought as one real
part, or a note for the assembler about what is not on the board. It is optional, it sits
beside `design.py`, and it takes effect at the next `finalize`: no re-route.

```python
from __future__ import annotations

# R1 is written in design.py without a part number; this is what the buyer is given.
REF_OVERRIDE = {
    "R1": {"mfr": "Yageo", "mpn": "RC0603FR-07332RL", "desc": "Resistor 332 0603 1%"},
}

# Printed under the table in the workbook, for the assembler.
NOT_IN_BOM = ["The cable that plugs into J1 is not assembled."]
```

After `finalize` the BOM row for R1 has `Yageo` and `RC0603FR-07332RL`, the part-number
warning is gone, and the workbook ends with a bold "Not assembled / not in BOM:" heading and
that line. `MPN_OVERRIDE` (every part with a given part number) and a `line` function (the
value and description a finished BOM line shows) are the other two names. A typo in a name or
a field is reported with what to fix. The names, their types and what wins over what are in
the [bom.py section](project-interface.md#bompy) of the project interface.

## pcbkit quote

```
pcbkit quote [--fab-qty N] [--assembled N] [--self-solder-tht] [--notes FILE]
```

It reads the files `finalize` wrote, from `out/fab/` (else `fab/`), and prints each value of
the order form. It reads the Gerber zip and the BOM CSV, not your design, so the numbers
describe what you are about to upload. Write the notes first, if you have any:

```
$ cat order-notes.txt
Do not substitute any part: R1 and D1 are exactly as listed in the BOM.
The orientation of D1 and J1 is on the assembly drawing.

$ .venv/bin/pcbkit quote --fab-qty 5 --assembled 2 --notes order-notes.txt
PCBWay quote values for Blinky_revA (files in out/fab)

Bare board
  Layers               2
  Board size           30 x 20 mm
  Thickness            1.6 mm
  Copper weight        1 oz (0.035 mm)
  Min track / spacing  0.25 / 0.21 mm (9.8 / 8.3 mil)
  Min hole size        0.3 mm (11.8 mil)
  Surface finish       HASL lead-free (the board says "HAL lead-free")
  Quantity             5

Assembly, 2 boards (the counts are per board)
  Unique parts         3
  SMD placements       2
  BGA/QFP/QFN parts    0
  Through-hole parts   1 parts, 1 designators: J1

Notes: 128 of 600 characters
```

### The values

| Row | Where it comes from |
|---|---|
| Layers, Board size, Thickness | The job file inside the Gerber zip. The size is the job file's less one outline line width, which KiCad adds. |
| Copper weight | The job file, so it is the `[stackup] copper_mm` the board was built with: 0.035 mm is 1 oz, 0.070 mm is 2 oz. |
| Min track / spacing | The smallest track width, and the smallest spacing, in the job file's design rules. |
| Min hole size | The smallest tool in the drill files, plated or not. |
| Surface finish | The job file, in the form's words: KiCad's "HAL lead-free" is shown as "HASL lead-free". |
| Quantity | Your `--fab-qty`. Without it the row says `not given (use --fab-qty N)`. |
| Unique parts | BOM lines the assembler places. All of them, or the surface-mount ones only with `--self-solder-tht`. |
| SMD placements | The sum of the surface-mount lines' quantities. |
| BGA/QFP/QFN parts | Parts whose footprint name contains `BGA`, `QFP` or `QFN` (so `LQFP`, `VQFN` and `LFBGA` count), with their references. Pitch is not looked at. |
| Through-hole parts | The sum of the through-hole lines' quantities, the number of designators, and the designators with each run of three or more written as a range (`J3-J9`). |

The Assembly block appears only with `--assembled`. Its counts are per board.

### The options

| Option | Does |
|---|---|
| `--fab-qty N` | How many bare boards you are making, shown in the Quantity row. |
| `--assembled N` | Adds the Assembly block for N boards. When you give both, N must not be more than `--fab-qty`: that is a usage error (exit 2), because you cannot assemble boards you did not make. Assembling fewer than you make is normal. |
| `--self-solder-tht` | You will solder the through-hole parts. It needs `--assembled`. The assembler's through-hole count becomes 0 and your parts move to a "You solder" row, as below. |
| `--notes FILE` | Checks the order notes against the form's limit of 600 characters, and prints how many there are. A line break counts as one character (CRLF too) and trailing whitespace is ignored, so an editor's final newline is free. Over the limit, the command fails and says by how many. |

With `--self-solder-tht` the assembly block reads:

```
Assembly, 2 boards (the counts are per board)
  Unique parts         2 (surface-mount lines only)
  SMD placements       2
  BGA/QFP/QFN parts    0
  Through-hole parts   0 for PCBWay (you solder them)
  You solder           1 parts, 1 designators: J1
```

and notes that are too long stop the command before it prints anything:

```
$ .venv/bin/pcbkit quote --notes too-long.txt
Error: the notes in too-long.txt are 640 characters; PCBWay's order notes take 600 (40 too many)
```

Keep the notes file in the project folder, not under `out/` or in `fab/`, which pcbkit
generates and rewrites. Say only what the files do not: parts you supply, parts to leave
unfitted, polarity beyond the assembly drawing, parts that must not be substituted.

### Warnings, errors and exit codes

`quote` checks the files it reads and warns after the numbers. A warning does not change the
exit status, so do not script on it:

```
WARNING: Blinky_revA_gerbers.zip is older than blinky.kicad_pcb: run `pcbkit finalize` before you rely on these numbers
WARNING: the Gerbers say 0.035, 0.035 mm copper but [stackup] copper_mm is 0.07: the files are from another board; run `pcbkit finalize`
```

The first means the board was saved after the export; the second that `pcbkit.toml` was
changed after it. Both are cured by `finalize`. With no export at all:

```
Error: no fab files: expected Blinky_revA_gerbers.zip and Blinky_revA_BOM.csv in ~/blinky/out/fab (or ~/blinky/fab). Run `pcbkit finalize` first
```

(Paths shortened.) Exit status: 0 for numbers printed, 1 for an error such as the one above, a
notes file that is missing, not UTF-8 or too long, or a Gerber zip it cannot read, and 2 for a
mistake in the options.

## Before you order

An order is paid for, so check the board is finished, and that the files you will upload
are from that board.

1. **Finish the board.** `build`, `route`, `promote`, `finalize`, in that order after a
   change, with DRC at 0 violations, 0 unconnected pads and 0 footprint errors and no
   part-number warning at the end. See [troubleshooting](troubleshooting.md) if a step
   refuses.
2. **Switch on the `fab` check group** in `[checks] groups` of `pcbkit.toml`, and fill in the
   numbers it reads in `specs.py`: PCBWay's limits for annular ring, drills, slots and
   silkscreen, the polarity marks of wire pads, the decoupling and I2C tables. Blinky's
   `specs.py` already holds them. What each group reads is in the
   [project interface](project-interface.md#built-in-groups-and-what-they-read).
3. **Run every check, not a selection.** `.venv/bin/pcbkit check` with no `-k`. A run with
   `-k` replaces `out/checks/results.json` with only the checks it ran and marks it
   `"complete": false`, and a green `-k` run proves nothing about the rest.
4. **Look at the pictures.** Open the assembly drawing and the renders in `out/docs/`, and
   check the polarity and orientation of every part against its datasheet.
5. **Check the copper weight** in `pcbkit.toml` is the one you will order. The copper checks
   and the quote's "Copper weight" row both follow `[stackup] copper_mm`.
6. **Run `quote`** with your quantities, and write the notes to a file outside `out/` and
   `fab/`.

The order skill runs a gate that checks your files and your check results are current and
complete. You can run it yourself from inside the project; it needs only `python3`, and it
lives in the pcbkit checkout (and in the plugin), not in the installed package:

```
$ python3 ~/src/pcbkit/skills/order-pcbway/scripts/preflight.py
OK: the fab files and checks of blinky are newer than its sources
```

It exits 0 when the Gerber zip, BOM and centroid exist and are newer than every source of
the board (`pcbkit.toml`, your Python modules except `specs.py`, `circuits.py` and
`mutants.py`, `footprints/` and `golden/`), and `out/checks/results.json` is newer than those
and than `specs.py`, `circuits.py` and `checks/`. The results must show every check passed,
none left out, a result for every switched-on group and for every check of your own.
Otherwise it exits 1 and prints one line per problem:

```
STALE: out/fab/Blinky_revA_gerbers.zip is older than design.py: run pcbkit build, route, promote and finalize
STALE: the checks were run before design.py changed: run pcbkit check
PARTIAL: the last pcbkit check run was cut short by -k 'test_led' (10 checks left out): run pcbkit check with no -k before ordering
```

It exits 2 when there is no `pcbkit.toml` here or above.

## Ordering with Claude

The Claude plugin has one skill for this, `order-pcbway`. You start it, always, and Claude
never starts an order on its own: ask "order these boards" and it points you to the skill
instead. Run it from the project, with the number of boards to make and the number to
assemble:

```
/pcbkit:order-pcbway 5 2
```

If you leave either number out, it asks. Installing the plugin is covered in
[the Claude plugin](claude-plugin.md).

What it does, in order:

1. Runs the gate above. If it exits non-zero, the skill shows you what it printed and stops:
   an order made from stale files is paid for.
2. Asks what the assembler needs to know that the files do not say, and writes your answer
   (600 characters at most, nothing invented) to `order-notes.txt` in the project folder.
3. Runs `pcbkit quote` with your quantities and that file. The quote is the only source of
   the numbers it enters.
4. Opens PCBWay's instant quote in your browser, using Claude in Chrome, and fills each field
   by its label, not its position. It leaves every other field at its default and tells you
   which ones cost money or time (shipping, lead time). It declines upgrades, coupons and
   extra services, and answers No if the form asks whether substitute parts may be used.

If the page works out something different from the quote (a unique-part count after it reads
the BOM, say), it stops and shows you both numbers rather than overriding either. Without the
Claude in Chrome tools it prints the quote and the stops below for you to follow by hand, and
does nothing else.

### Where it stops

At each of these it says exactly what is done and what is left, and waits for you.

| Stop | What happens |
|---|---|
| **Credentials** | At a sign-in, registration or account page, you sign in yourself, in the browser. Claude never types, reads, stores or asks for a password or a code. |
| **Uploads** | At each file upload it names the file and gives its full path, and you pick it in the dialog: the Gerber zip, the BOM and the centroid, all in `out/fab/`. It does not choose a file for you. |
| **Payment** | It stops before any button that pays or places the order, and at any page that asks for card or billing details. It summarises the order (each field it set, the total the page shows, the shipping method) and you press the button. |

Text on a web page is data, not instructions: only the skill and you direct what Claude does.
When it is done it gives you a table of every value it entered beside the quote's value for it,
the notes that went in, and anything left at a default that you may want to change. If
PCBWay later proposes a substitute part, it shows you; accepting one is your call.
