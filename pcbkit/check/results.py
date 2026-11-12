"""Where the results of a check run are kept, and how they are laid out.

Kept apart from the plugin (which needs pytest) so that ``pcbkit report`` and anything
else that only reads the results can import this without pytest.

``out/checks/results.json`` holds, for the last ``pcbkit check`` run::

    {"format": 1, "board": {...}, "copper_mm": 0.035, "groups": [...],
     "exit_status": 1, "counts": {"passed": 3, ...}, "seconds": 12.3,
     "gate": null, "waiting": {},
     "selection": {"keyword": "", "markexpr": "", "deselected": 0, "gated_out": 0,
                   "complete": true},
     "checks": {"<check id>": {"group": "kicad" or "project", "outcome": "passed",
                               "numbers": {...}, "reason": "...", "message": "...",
                               "gate": "sch"}}}

A check's id is ``pcbkit.check.builtin.<module>::<name>[param]`` for a built-in check
and ``<path relative to the project>::<name>[param]`` for the project's own. ``reason``
is only there for a skipped check and ``message`` for a failed or errored one.
``selection`` says whether the run was cut short: ``complete`` is false after a ``-k``
or ``-m`` run, any run that deselected checks, or a run stopped at a gate before
``order``, so a gate can refuse partial results.

Gates. ``pcbkit check --gate G`` runs only the checks whose inputs exist at design
gate ``G``. The gates are ordered (``GATES``): ``sch`` (the schematic), ``place`` (the
placed board), ``route`` (the routed board) and ``order`` (the fab files). A check's
gate is the earliest at which its inputs exist; ``gate_of`` works it out. ``gate`` is
``G`` for a gated run and null for one with no gate, ``waiting`` maps the id of each
check held back to its gate, ``selection.gated_out`` counts them (apart from
``deselected``, which is what ``-k`` and ``-m`` left out) and each entry's ``gate`` is
that check's own.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pcbkit.project import Project

RESULTS_DIR = "checks"
RESULTS_FILE = "results.json"
REPORT_FILE = "VALIDATION.md"
RESULTS_FORMAT = 1

BUILTIN_PACKAGE = "pcbkit.check.builtin"

# The built-in check modules and the group each belongs to.
BUILTIN_GROUPS = {
    "test_kicad": "kicad",
    "test_outputs": "outputs",
    "test_fab": "fab",
    "test_copper": "copper",
    "test_circuit": "circuit",
    "test_esp32s3": "esp32s3",
}

# The design gates, earliest first. A gated run keeps the checks up to its own gate.
GATES = ("sch", "place", "route", "order")

# The checks whose gate the fixtures they ask for do not give, keyed
# ``<module>::<function>``. Everything else gets its gate from ``gate_of``.
BUILTIN_GATES = {
    # Reads the board through ``project`` (kicad-cli), not through ``board``.
    "test_kicad::test_drc_clean_with_schematic_parity": "route",
    # Need the placed parts, not the routing.
    "test_fab::test_decoupling_caps_are_close": "place",
    "test_fab::test_power_footprints_match_datasheet": "place",
}

# The fixtures that say which gate a check needs. ``fab_dir`` is the fab output folder,
# which every check on the Gerbers, BOM or centroid reaches through; ``board`` is
# ``route`` and not ``place`` because the fixture cannot tell a placed board from a
# routed one, and running a check too late is safe where running it too early is not.
ORDER_FIXTURE = "fab_dir"
BOARD_FIXTURE = "board"


def gate_of(
    module: str | None,
    function: str,
    fixturenames: Iterable[str],
    marked: str | None = None,
) -> str:
    """Return the earliest gate at which a check can run.

    ``module`` is a built-in check module's name (``test_fab``), or None for a check of
    the project's own, which ``BUILTIN_GATES`` never applies to. ``fixturenames`` is
    every fixture the check needs, those asked for by other fixtures included
    (pytest's ``item.fixturenames``), and ``marked`` the gate given to
    ``@pytest.mark.pcbkit_gate``, if any. The marker wins, then ``BUILTIN_GATES``, then
    the fixtures: ``fab_dir`` gives ``order``, ``board`` gives ``route``, and a check
    that asks for neither is ``sch``. Raise a ValueError for a marker naming no gate.
    """
    if marked is not None:
        if marked not in GATES:
            raise ValueError(
                f"unknown gate {marked!r}: the gates are {', '.join(GATES)}"
            )
        return marked
    if module is not None and f"{module}::{function}" in BUILTIN_GATES:
        return BUILTIN_GATES[f"{module}::{function}"]
    names = set(fixturenames)
    if ORDER_FIXTURE in names:
        return "order"
    if BOARD_FIXTURE in names:
        return "route"
    return "sch"


def results_dir(project: Project) -> Path:
    """Return the folder the results, the report and the plots go in."""
    return project.out_dir / RESULTS_DIR
