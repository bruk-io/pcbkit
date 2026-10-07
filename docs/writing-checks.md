# Writing checks

A check is a pytest function that fails when the board breaks a rule. pcbkit ships checks
of its own (ERC and DRC, the fab files, PCBWay's limits, copper, circuit rules) and runs
yours in the same pass, so one `pcbkit check` says whether the board is fit to order. This
page is about writing yours, and about the habit that makes a check worth having: watch it
fail before you trust it.

The file formats are in the [project interface](project-interface.md). This page shows how
they fit together, using the blinky example (`examples/blinky/` in the pcbkit repository: a
connector, a resistor and an LED).

## Where things go

| File | What it holds |
|---|---|
| `checks/test_*.py` | Your checks: ordinary pytest functions. Nothing to register. |
| `specs.py` | The numbers they read: datasheet values and design limits, each with its source. |
| `mutants.py` | Planted mistakes that show each check can fail. |
| `circuits.py` | Optional: operating points for the circuit checks. |

`pcbkit check` runs the built-in checks of the groups in `[checks] groups`, then every
`test_*.py` under `checks/`. The smallest check:

```python
def test_j1_carries_vin(nl):
    assert nl.net("J1", 1) == "VIN", "J1 pin 1 should be on VIN"
```

pytest hands `nl` to the function because the argument has that name: it is a fixture.

Before you write a check, see whether a built-in one already covers the hazard. This lists
them (a file's name gives its group, and a group reads its numbers from `specs.py`):

```
grep -n '^def test_' "$(.venv/bin/python -c 'import pcbkit.check.builtin as m; print(m.__path__[0])')"/*.py
```

If one fits, switch its group on in `pcbkit.toml` and fill in the numbers it reads
([Built-in groups and what they read](project-interface.md#built-in-groups-and-what-they-read)
lists them) instead of writing a second check for the same thing.

## Fixtures

These come from pcbkit's check plugin. Run checks with `pcbkit check`, not plain `pytest`:
only `pcbkit check` loads the plugin.

| Fixture | What you get |
|---|---|
| `nl` | The netlist of a fresh export of the schematic: `nl.parts`, `nl.nets`, `nl.net(ref, pin)`, `nl.refs_on(net)`, `nl.kind(ref)`, `nl.by_kind("Device:R", ...)`. Net names have no leading `/`. |
| `board` | The routed board, loaded with pcbnew from `kicad/<stem>.kicad_pcb`. |
| `specs`, `circuits`, `layout` | Your `specs.py`, `circuits.py` and `layout.py`. |
| `project` | The project: `root`, `config`, `kicad_dir`, `out_dir`. |
| `record` | `record(key, value)` keeps a number for `results.json` and the report. |
| `out_dir` | `out/checks/`, where plots go. |

All of them last for the whole run except `record`, which belongs to one check. Two things
to know:

- `nl` is read from the schematic that `pcbkit sch` (or `build`) last wrote, not from
  `design.py`. After you change `design.py`, run `pcbkit sch` before `pcbkit check`.
- `board` is the finished board, so use it after `route` or `finalize`. A position you read
  from it is in KiCad's file coordinates, which are layout millimetres plus (50, 50):
  `pcbkit.kicad.board.to_local(pad.GetPosition())` gives layout millimetres.

A check on copper reads `board`. This one fails if the router made any VIN track narrower
than the net class in `routing.py` asks for. With `VIN_TRACK_MM = 0.4` in `specs.py`
(blinky's `Supply` net class routes VIN at 0.4 mm) it passes on blinky's board:

```python
def test_vin_tracks_keep_their_class_width(board, specs, record):
    import pcbnew

    widths = [
        pcbnew.ToMM(item.GetWidth())
        for item in board.GetTracks()
        if item.GetClass() == "PCB_TRACK" and item.GetNetname() == "/VIN"
    ]
    assert widths, "VIN has no track on the board: route it first"
    record("VIN track widths (mm)", sorted(set(widths)))
    assert min(widths) >= specs.VIN_TRACK_MM, f"VIN has a {min(widths):g} mm track"
```

Net names read from the board keep KiCad's leading `/`, as in `"/VIN"`.

To run a check once per row of a table in `specs.py`, use `spec_params` from
`pcbkit.check.plugin`; [Writing a check](project-interface.md#writing-a-check) has an
example. A table that is missing then fails the check by name instead of making it vanish
from the run.

## specs.py: numbers with their sources

`specs.py` is plain Python: names and values. Put each limit there with its source beside
it (the datasheet, the page, the date you read it), so a reader can tell a limit from a
guess. A limit you made up is worse than none, because it looks like evidence. This is the
LED's block from blinky:

```python
# Kingbright APT1608SGC, datasheet DSAD0932 rev V.22B (2023-12-07), page 2.
# Forward voltage at 20 mA: 2.2 V typical, 2.5 V maximum. It gives no minimum, so the
# lowest voltage is the typical one less the +-0.1 V of the measurement and the 0.12 V
# the voltage falls by at 85 C (-2.0 mV/C), rounded down. A lower voltage means more
# current, so this is the safe end for the brightest case.
LED_VF_MAX = 2.5
LED_VF_MIN = 1.9
# Absolute maximum DC current is 25 mA, and the permitted current falls as the air
# warms (page 3): 20 mA, the datasheet's test current, leaves room for a warm room.
LED_I_MAX = 0.020
# The dimmest current that still reads as lit (the LED is 12 mcd typical at 20 mA).
LED_I_MIN = 0.001
```

In a check, `specs.LED_I_MAX` returns the value. A name that is not defined fails the check
and says which file and name are missing:

```
E   pcbkit.check.plugin.MissingSpec: specs.LED_I_MAX is not defined in /path/to/blinky/specs.py: add it (docs/project-interface.md says what each check group reads)
```

`specs.get("NAME", default)` and `"NAME" in specs` ask without failing. The built-in groups
read `specs.py` the same way: a group you switch on whose names are missing does not skip,
it fails, naming each one.

## Running checks

`check` and `mutants` need pcbnew, so run them from the project's own `.venv`, which
`pcbkit setup` makes. `report` only reads a file and runs anywhere.

```
.venv/bin/pcbkit check                              # the built-in groups you switched on, then checks/
.venv/bin/pcbkit check -k test_led_current_window   # only the checks that match
pcbkit report                                       # write out/checks/VALIDATION.md
```

`-k` is pytest's own expression (`-k led`, `-k "led or resistor"`, `-k "not drc"`). The
exit code is pytest's: 0 all passed, 1 a check failed, 5 nothing matched.

Every run writes `out/checks/results.json`: one entry per check with its outcome, the
message of a failure or the reason for a skip, and the numbers it recorded. `pcbkit report`
turns it into `out/checks/VALIDATION.md`, a table per group and per file of checks. It
lists what the last run ran and nothing more, and it exits 1 if a check failed.

A `-k` run replaces the results with the few checks it ran, and says so in the file:

```json
"selection": {"keyword": "test_led_current_window", "markexpr": "", "deselected": 10, "complete": false}
```

Treat those results as partial. The report written from them reads like any other (it does
not say it is partial), and the order preflight refuses results with `complete: false`
([ordering](ordering.md)). Run `pcbkit check` with no `-k` before you order.

### When a check fails

- Read the `E` lines of the output, or the `message` in `results.json`. A good message
  names the part, the value and the limit.
- Do not loosen the check, widen a tolerance, or delete it to get a green run. The failure
  is the information: fix the design, the layout or the routing.
- If the limit itself is wrong, change it in `specs.py` with a source for the new number.
- Skip only when a check does not apply to this board, with
  `pytest.skip("D1 is off-board on this revision")`. The reason is listed in
  `results.json`.

## Prove a check can fail

A check that has never been seen to fail is not trusted: it may pass because it looks at
the wrong thing. `mutants.py` lists planted mistakes, each a small edit to `design.py`,
with the check that must then fail:

```python
MUTANTS = [
    (
        "R1 is 10 ohm: the LED is overdriven",  # what the mistake is
        [  # edits to design.py: (text to find, text to put there)
            (
                'R("R1", "330", "VIN", "LED_A", B, "RC0603FR-07330RL"',
                'R("R1", "10", "VIN", "LED_A", B, "RC0603FR-0710RL"',
            ),
        ],
        "test_led_current_window",  # the -k expression that must then fail
    ),
]
```

`pcbkit mutants` first runs the control: the checks the entries name, on an unedited
scratch copy of the project, must pass. Then, for each entry, it plants the edits in
another scratch copy, makes the schematic again there and runs the checks. A mistake is
**caught** when a check fails. It is **missed** when every check still passes, or when the
text to find is not in `design.py`, so a stale entry cannot pass for a catch. The exit code
is 0 when everything is caught, 1 when one is missed and 2 when the control does not pass.

Mutants edit `design.py` only and leave the layout alone, so they test the schematic,
netlist and circuit checks. To show that a copper check fails, plant the mistake in a
scratch copy of the project instead: copy it without `.venv`, narrow the track or move the
part there, rebuild what the check reads, and run the check from inside the copy with the
original project's `.venv/bin/pcbkit`. Never plant a mistake in the project itself.

## Worked example: the LED's current in blinky

**The hazard.** An LED must carry enough current to be seen and no more than its datasheet
allows, whatever the supply does. Blinky's supply may be anything from 3 V to 5.5 V, so the
question is asked at both ends: the dimmest case is the lowest supply with the highest
forward voltage, and the brightest is the highest supply with the lowest forward voltage.

**The numbers** are the block of `specs.py` above, plus the supply: `SUPPLY_NET = "VIN"`,
`GROUND_NET = "GND"` and `SUPPLY_V = (3.0, 5.5)`.

**The check**, from `checks/test_indicator.py` (its module docstring left out). `dc` is
pcbkit's DC solver: `Circuit.fix` holds a net at a voltage, `R` adds a resistor, `D` a
diode (a forward voltage plus a small resistance), and `solve()` returns a `Solution` that
knows each diode's current.

```python
from __future__ import annotations

from typing import Any

from pcbkit.check import dc
from pcbkit.check.netlist import Netlist, parse_value
from pcbkit.check.plugin import ProjectModule


def solve(nl: Netlist, specs: ProjectModule, vin: float, vf: float) -> dc.Solution:
    """Return the DC operating point with VIN at ``vin`` and every LED dropping ``vf``.

    Built here from the netlist and specs.py rather than with ``dc.build``, which gives
    an LED a forward voltage by colour (2.9 V for green) and 20 ohm, where this LED's
    datasheet says 2.2 V typical and 2.5 V at most. Resistors take their values from
    the netlist. An LED is a fixed forward voltage plus 1 ohm, which the solver wants
    above zero.
    """
    circuit = dc.Circuit()
    circuit.fix(specs.SUPPLY_NET, vin).fix(specs.GROUND_NET, 0.0)
    for ref in nl.by_kind("Device:R"):
        circuit.R(
            nl.net(ref, 1), nl.net(ref, 2), parse_value(nl.parts[ref]["value"]), ref
        )
    for ref in nl.by_kind("Device:LED"):
        # KiCad's LED symbol: pin 1 is the cathode, pin 2 the anode
        circuit.D(nl.net(ref, 2), nl.net(ref, 1), vf, 1.0, ref)
    return circuit.solve()


def test_led_current_window(nl: Netlist, specs: ProjectModule, record: Any) -> None:
    """Light every LED at the lowest supply and keep it under its limit at the highest.

    The dimmest case is the lowest VIN with the highest forward voltage, the brightest
    the highest VIN with the lowest forward voltage. A reversed LED, or a supply that
    cannot reach the forward voltage, carries no current and fails the low end.
    """
    low, high = specs.SUPPLY_V
    cases = {"dimmest": (low, specs.LED_VF_MAX), "brightest": (high, specs.LED_VF_MIN)}
    leds = nl.by_kind("Device:LED")
    assert leds, "the design has no LED"
    found: dict[str, float] = {}
    for name, (vin, vf) in cases.items():
        solved = solve(nl, specs, vin, vf)
        for ref in leds:
            found[f"{ref} {name}"] = solved.diode_current(ref)
    record("LED mA", {key: round(amps * 1e3, 2) for key, amps in found.items()})
    bad = {
        key: round(amps * 1e3, 2)
        for key, amps in found.items()
        if not specs.LED_I_MIN <= amps <= specs.LED_I_MAX
    }
    assert not bad, (
        f"LED current (mA) outside {specs.LED_I_MIN * 1e3:g} to "
        f"{specs.LED_I_MAX * 1e3:g} mA: {bad}"
    )
```

The built-in `circuit` group has an LED check too: it reads `LED_REFS`, `LED_SCENARIO`,
`LED_I_MIN` and `LED_I_MAX` and a `scenario` function in `circuits.py`. Use that one when
the stock LED model suits your part. Blinky's own check exists because `solve` needs this
LED's datasheet voltages, which is what its docstring says.

Three things to copy from it. The limits come from `specs`, never from the check. The
failure message names the part, the case and the numbers. And `record` keeps the currents
for the report, so a pass shows how much margin there is.

**Run it.**

```
$ .venv/bin/pcbkit check -k test_led_current_window
.                                                                        [100%]
1 passed, 10 deselected in 0.45s
Results: out/checks/results.json (pcbkit report)
```

`results.json` now holds `"LED mA": {"D1 dimmest": 1.51, "D1 brightest": 10.88}` for it:
the LED is lit at 3 V, and at 5.5 V it is at about half its limit.

**Make it fail.** Blinky's `mutants.py` plants three mistakes: R1 at 10 ohm, named once
for this check and once for the resistor's power check, and D1 turned the wrong way round:

```
$ .venv/bin/pcbkit mutants
control (no edits)                                             PASSES  2 passed, 9 deselected in 7.44s
R1 is 10 ohm: the LED is overdriven                            CAUGHT  (1 failed, 10 deselected in 3.24s)
R1 is 10 ohm: the resistor overheats                           CAUGHT  (1 failed, 10 deselected in 0.78s)
D1 is the wrong way round: the LED never lights                CAUGHT  (1 failed, 10 deselected in 0.71s)

3/3 planted mistakes caught
```

To read the failure yourself, make the 10 ohm edit in a scratch copy of the project, run
`pcbkit sch` there, then `pcbkit check -k test_led_current_window` from inside the copy:

```
E       AssertionError: LED current (mA) outside 1 to 20 mA: {'D1 dimmest': 45.45, 'D1 brightest': 327.27}
```

**Add a mistake of your own.** The window has a bottom edge too, and none of the three
plants a mistake against it. At 1 kohm the LED gets under 1 mA at 3 V. Add this line above
`MUTANTS` in `mutants.py`, next to `R1_AT_10_OHM`:

```python
R1_AT_1K = 'R("R1", "1k", "VIN", "LED_A", B, "RC0603FR-071KL"'
```

and this entry inside it, then run `pcbkit mutants` again:

```python
    (
        "R1 is 1 kohm: the LED is too dim at 3 V",
        [(R1, R1_AT_1K)],
        "test_led_current_window",
    ),
```

```
R1 is 1 kohm: the LED is too dim at 3 V                        CAUGHT  (1 failed, 10 deselected in 0.67s)
```

Name `test_resistor_power_margin` in that entry instead and the mistake is reported as
`MISSED`: the resistor is fine at 1 kohm, so the check you named cannot see it. An entry
whose text to find is no longer in `design.py` is reported as missed too, with `SETUP
ERROR: pattern not found in design.py`.

If you use Claude Code, the pcbkit plugin's `add-check` skill and `check-writer` agent
follow the rules on this page; see [the Claude plugin](claude-plugin.md).
