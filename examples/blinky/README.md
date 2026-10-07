# Blinky

The smallest board pcbkit can take from Python to Gerbers: a 2-pin power connector (J1,
VIN and GND), a 330 ohm resistor (R1) and a green LED (D1) on a two-layer board, 30 x 20 mm.
The supply may be 3 V to 5.5 V; R1 then holds the LED between 1.5 mA and 11 mA.

It is a real board, not a stub. The schematic, the placement, the route, the ground pours,
the silkscreen, the Gerbers, the bill of materials and the pick-and-place file all come
from the Python files in this folder, and `pcbkit check` judges the result. It is also the
board that `pcbkit new` copies when you start your own.

## What is where

| File | What it says |
|---|---|
| `pcbkit.toml` | The board's name and revision, the stackup, the router and stitching settings, and which check groups run. |
| `design.py` | The circuit: three parts, and the net on each pin. |
| `layout.py` | The board's size and rounded corners, and where each part sits. |
| `routing.py` | What routing needs from the project: a wider net class for VIN, the clearance PCBWay needs, and the two ground pours. |
| `silk.py` | The silkscreen text: the title, the supply label, and the + and - marks at J1. |
| `specs.py` | The numbers the checks read, each with its source: the LED's datasheet, the resistor's rating, PCBWay's limits. |
| `checks/test_indicator.py` | Two checks of its own: the LED's current stays between 1 mA and 20 mA across the supply range, and R1 stays under half its power rating. |
| `mutants.py` | Three planted mistakes that those checks must catch. |
| `golden/` | The route that passed DRC. `pcbkit finalize` rebuilds the board from it. |
| `pyproject.toml` | Makes the project's `.venv` on KiCad's Python and installs pcbkit into it. |

`kicad/` and `out/` are made by the commands below and are not kept in git.

## Try it

You need KiCad 10, Java 17 or newer, and [uv](https://docs.astral.sh/uv/);
`pcbkit doctor` says what is missing. From a pcbkit checkout, once `uv sync` has made
its own `.venv`:

```
cd examples/blinky
uv run --project ../.. pcbkit setup     # .venv on KiCad's Python, and Freerouting
.venv/bin/pcbkit build          # schematic, ERC, netlist and placement
.venv/bin/pcbkit route          # Freerouting, the pours and DRC
.venv/bin/pcbkit promote        # keep the route that passed as golden/
.venv/bin/pcbkit finalize       # the board from golden/, DRC, Gerbers, BOM, renders
.venv/bin/pcbkit check          # 17 pass, 2 skip
.venv/bin/pcbkit mutants        # plant each mistake: all must be caught
```

`finalize` works straight after `build`, because `golden/` is already here; run `route` and
`promote` again after you change `design.py` or `layout.py`.

Two of the `fab` checks are skipped: blinky has no IC to decouple and no I2C bus, so the
tables they read in `specs.py` are empty. Add rows when you add those parts.
