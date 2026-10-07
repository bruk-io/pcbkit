# Changelog

All notable changes to pcbkit. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and pcbkit uses
[semantic versioning](https://semver.org/).

## [0.1.0] - 2026-10-07

The first release.

### Added

- A board is a small project of Python modules and a `pcbkit.toml`: `design.py` (the
  circuit), `layout.py` (where parts go), `routing.py` (hand routes, keep-outs and
  pours), `silk.py`, and optional `bom.py`, `footprints.py`, `specs.py`, `checks/` and
  `mutants.py`.
- Commands for the whole flow: `new`, `setup`, `doctor`, `sch`, `build`, `route`,
  `promote`, `finalize`, `check`, `mutants`, `report`, `quote`, `compare` and `shots`.
- Schematic generation with ERC and the netlist, placement from `layout.py`, routing with
  Freerouting (retries, a stall watchdog, and eco routes that keep a previous route),
  ground pours with stitching, the silkscreen, DRC with schematic parity, and the fab
  files: BOM, centroid, Gerbers and drill files, PDFs and 3D renders.
- Checks run with pytest: built-in groups (KiCad, outputs, fab, copper, circuit and an
  ESP32-S3 pack) driven by the project's `specs.py`, the project's own checks, and
  mutants that prove the checks catch planted mistakes.
- `pcbkit quote` prints every number the PCBWay quote form asks for, and checks that
  the order notes fit its 600 characters.
- A Claude Code plugin: skills for the board workflow, starting a board, adding checks,
  ordering from PCBWay and reviewing a board; two agents (a parts researcher and a
  check writer); and hooks that keep edits out of generated files and keep the check
  writer inside `checks/`.
- An example board, `examples/blinky`, which `pcbkit new` copies.
- Documentation, published per version to GitHub Pages.
