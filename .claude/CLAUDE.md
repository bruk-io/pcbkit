# pcbkit: development notes

pcbkit designs, places, routes, checks and exports KiCad boards from Python. A board is a
small project of Python modules plus a `pcbkit.toml`; pcbkit generates the schematic,
placement, routing, checks and fab files for KiCad from it. This file is for working on
pcbkit itself.

- `docs/project-interface.md` is the board-project contract: the folder layout, the
  `pcbkit.toml` schema and the `routing.py` hooks. Keep it in step with
  `pcbkit/project.py`; a unit test checks that every schema key is documented with its
  type and default.
- There is no root `CLAUDE.md` on purpose: the repo is meant to double as a Claude Code
  plugin, and `claude plugin validate` warns about a `CLAUDE.md` at a plugin's root.

## Commands

```
uv sync
uv run pcbkit --help
uv run pcbkit doctor                        # what is installed on this machine
uv run pytest tests/unit -q                 # no KiCad needed
uv run --python 3.9 pytest tests/unit -q    # the 3.9 floor
uv run pytest -m "not kicad and not e2e" -q # everything CI runs
uv run pytest tests/integration -m kicad    # real KiCad, but only the tests that need no pcbnew
.venv-kicad/bin/python -m pytest -m kicad -q  # real KiCad and pcbnew: see "Tests that need pcbnew"
uv run ruff check . && uv run ruff format --check .
```

`uv run --python 3.9 ...` recreates `.venv` on 3.9, and the next plain `uv run` flips it
back to the pinned 3.14 (`.python-version`). Check `uv run python --version` before
trusting a result.

## Tests that need pcbnew

`pcbnew` only imports under KiCad's own Python, which `uv run` never uses, so the tests
that build and check real boards (`tests/integration/test_kicad_core.py`) run in a second,
untracked environment, `.venv-kicad`, made on KiCad's interpreter with its site-packages
visible:

```
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
uv venv --python $KICAD_PY --system-site-packages .venv-kicad
VIRTUAL_ENV=.venv-kicad uv pip install -e . pytest
.venv-kicad/bin/python -m pytest -m kicad -q
```

In any other Python those modules skip themselves at import (`pytest.importorskip`), with
the reason on the skip line, and CI skips them too. `kicad-cli` must be findable
(`pcbkit doctor` says). Hazards that break a whole process, such as freeing a removed
item, are shown in a child Python so they cannot take the test run down with them.

## Python 3.9 floor

`requires-python = ">=3.9"` is a deliberate exception to the usual 3.14 default: `pcbnew`
only imports under the Python bundled with KiCad (3.9.13 in KiCad 10 on macOS), and
`pcbkit setup` builds a board project's venv on that interpreter. All code must therefore
run on 3.9 and 3.14:

- `from __future__ import annotations` in every module (ruff enforces it).
- No `match`; no `X | Y` outside annotations (`isinstance`, aliases, `cast`); no
  `dataclass(slots=..., kw_only=...)`, `zip(strict=...)`, `typing.Self`/`TypeAlias`,
  `contextlib.chdir`; no `get_type_hints()` on `X | Y` annotations.
- `tomllib` on 3.11+, `tomli` before that (`pcbkit/project.py` shows the pattern).
- Click is 8.1.x on 3.9 and 8.5 on 3.14. In tests assert on `result.output` and never pass
  `mix_stderr`.

## Layout

```
pcbkit/cli.py         click group; one function per command; a stub fails with
                      "not implemented yet (WPn)", naming the work package that fills it in
pcbkit/project.py     find pcbkit.toml, validate it, import the project's modules by path
pcbkit/doctor.py      `pcbkit doctor`: the checks, fix hints and report text
pcbkit/kicad/env.py   find KiCad, kicad-cli, KiCad's Python, Java, Freerouting, ngspice, ...
pcbkit/kicad/sexp.py  parse and write KiCad s-expressions
pcbkit/kicad/cli.py   kicad-cli wrappers (ERC, netlist, DRC, Gerbers, drill, positions, SVG,
                      3D render) and the pure parsers for the ERC and DRC reports
pcbkit/kicad/board.py pcbnew helpers (units, nets, tracks, vias, zones, keep-outs, text)
                      and the shields for KiCad 10's hazards; imports without pcbnew
pcbkit/design.py      the design DSL (part, R, C, LED, FP) and load_design -> Design
pcbkit/libs.py        the project's own symbol and footprint libraries, lib tables, project file
pcbkit/sch.py         schematic generator, ERC, netlist; build_schematic is `pcbkit sch`
docs/                 project-interface.md
tests/unit/           one module each, nothing real touched; the fake machine is automatic
tests/integration/    several modules together; the tests marked kicad need real KiCad
tests/fake_machine.py, tests/conftest.py   the `machine` fixture: a fake PATH, HOME and OS
tests/fake_pcbnew.py  a pcbnew stand-in (unit tests): the `fake_pcbnew` fixture installs it
tests/tiny_board.py   builds a small real board and schematic, in production order
tests/fixtures/reports/  real KiCad 10.0.6 ERC and DRC reports from generic boards
tests/board_files.py, tests/netlist_norm.py   write a throwaway project; reduce a netlist
tests/fixtures/       golden/ (schematic generator), tiny_board/ (real-KiCad `pcbkit sch`)
```

`tests/fixtures/golden/expected.kicad_sch` was made by the generator that `sch.py` was
moved from, and the port reproduces it byte for byte; regenerate it only if the generator
is meant to change.

## Rules

- Two tiers. Tier 1 (new, doctor, setup, sch, quote) runs anywhere. Tier 2 (build, route,
  promote, finalize, check, mutants, compare, shots) needs `pcbnew`, so its first line must
  be `pcbkit.kicad.env.require_pcbnew()`. The stubs do not call it yet, so they say "not
  implemented yet"; whoever implements a tier 2 command adds the call.
- Style: uv for everything, ruff (black profile, 88 columns), click, type hints on every
  function, imperative docstrings (D401), plain functions and data (classes only for real
  state).
- Tests: never skip, loosen or mock away the thing under test. A unit test fakes every
  external dependency (PATH, subprocess, HOME, platform) and passes on a machine with no
  KiCad; a test that needs real KiCad is `@pytest.mark.kicad`; a slow real-tool run is
  `@pytest.mark.e2e`. A new check needs a test that makes it fail.
- Prove a test can fail: plant the mistake it guards against and watch it go red. If you
  edit source in place to do that, delete `__pycache__` between runs: a `.pyc` is trusted
  by whole-second mtime and size, so an equal-size edit that is undone within the second
  is served stale and the next run tests the mistake, not the code.
- pcbnew hazards (details in the `pcbkit/kicad/board.py` docstring): never call
  `HitTest` (a unit test scans `pcbkit/` for it; use `board.point_in_track`); take items
  off a board with `board.remove` and never let go of them; go through `board.mm` and
  `board.pt` so numpy scalars become floats; write the stackup copper with
  `board.set_copper` after the last `SaveBoard`. Layout millimetres and KiCad file
  millimetres differ by (50, 50): reports and saved files hold the latter.
- Report fixtures are real kicad-cli output on small generic boards. Make a new one by
  running kicad-cli on one, then rename the board and anything specific.
- Generated files in a board project (`kicad/`, `out/`, `golden/`, `fab/`) are never
  hand-edited.
- Examples in docs and tests are generic (`my-board`, plain part references). Never paste
  in content from a real board.
- Canadian/British spelling and plain hyphens (no em dashes) in prose.
- One branch per work package, `wp<N>-<slug>`, small commits. Don't push or publish
  without the owner's say-so.
