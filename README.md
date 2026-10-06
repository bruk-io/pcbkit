# pcbkit

pcbkit designs, places, routes, checks and exports KiCad boards from Python. A board
is a small project of plain Python modules and one `pcbkit.toml`; the schematic,
netlist, layout, routed copper, fabrication files and a suite of design checks are all
generated from it, so a change is a code change you can review and re-run.

**Work in progress.** Nothing is released yet. The command line exists, but only
`pcbkit doctor` does anything so far. The board-project contract is in
[docs/project-interface.md](docs/project-interface.md).

Licensed under AGPL-3.0-or-later; see [LICENSE](LICENSE).
