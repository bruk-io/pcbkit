#!/usr/bin/env python3
"""Say which check groups a gate needs, which are off, and the run for that gate.

``pcbkit check`` collects no built-in check of a group that ``[checks] groups`` leaves
out, so a ``-k`` selection over such a group runs nothing and decides nothing, without
a word. This script owns, per gate, the groups its checks need and the ``-k`` selection
that runs them, so no review spells either out. Run it from the project folder, under
the project's own Python:

    .venv/bin/python "<plugin>/skills/board-workflow/scripts/groups.py" --gate sch

The gates are ``sch`` (the schematic is ERC-clean), ``place`` (after ``pcbkit build``),
``route`` (a routed board) and ``order`` (every check, before an order). The
``esp32s3`` group is added to each when ``specs.py`` defines ``DEVKIT_REF``. It prints:

* ``ON:`` or ``OFF:`` with the groups the gate needs, and which of them are off;
* ``RUN:`` the command for the gate: a ``-k`` selection, or a full ``pcbkit check``;
* two ``NOTE:`` lines: a group just switched on fails by name each table it lacks, and
  a ``-k`` run is partial.

Exit codes: 0 every group the gate needs is on, 1 at least one is off, 2 an unknown
gate or group, no board project here, or a ``specs.py`` that cannot be imported.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from pcbkit.project import (
    CHECK_GROUPS,
    ProjectError,
    import_optional_project_module,
    load_project,
)

PLACED = (
    "test_circuit or test_esp32s3 or test_erc_clean or "
    "test_decoupling_caps_are_close or test_power_footprints_match_datasheet or "
    "test_filter_caps_sit_by_the_adc_pin"
)
# Per gate: the check groups it needs, and its -k selection (None: run every check).
GATES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "sch": (("kicad", "circuit"), "test_circuit or test_esp32s3 or test_erc_clean"),
    "place": (("kicad", "circuit", "fab", "copper"), PLACED),
    "route": (("kicad", "circuit", "fab", "copper"), "not test_outputs"),
    "order": (("kicad", "circuit", "fab", "copper", "outputs"), None),
}
PYTHON_CHECK = ".venv/bin/pcbkit check"
SWITCHED_ON = (
    "NOTE: a group just switched on fails by name each table it lacks: write those "
    "first"
)
PARTIAL = (
    "NOTE: a -k run is partial: it replaces out/checks/results.json with only the "
    "checks it ran, so a check whose inputs do not exist yet is not a finding, a "
    "project check needs `or test_<topic>` added, and the order gate runs them all"
)


def needed_groups(gate: str, devkit: bool) -> list[str]:
    """Return the groups ``gate`` needs, and ``esp32s3`` too on a DevKit board."""
    groups = list(GATES[gate][0])
    return groups + ["esp32s3"] if devkit else groups


def command(gate: str) -> str:
    """Return the ``pcbkit check`` command for ``gate``."""
    selection = GATES[gate][1]
    return PYTHON_CHECK if selection is None else f'{PYTHON_CHECK} -k "{selection}"'


def report(on: Sequence[str], needed: Sequence[str]) -> tuple[int, str]:
    """Return the exit code and the line for groups ``needed`` when ``on`` are on."""
    unknown = [name for name in needed if name not in CHECK_GROUPS]
    if unknown or not needed:
        return 2, (
            f"UNKNOWN: {', '.join(unknown) or 'no group named'}: name check groups "
            f"from {', '.join(CHECK_GROUPS)}"
        )
    off = [name for name in needed if name not in on]
    if off:
        return 1, (
            f"OFF: {', '.join(off)} (pcbkit.toml [checks] groups is "
            f"{', '.join(on) or 'empty'}): add them before the first run, since a "
            "group that is off collects no check and a -k selection over it decides "
            "nothing"
        )
    return 0, f"ON: {', '.join(needed)}"


def lines(gate: str, on: Sequence[str], devkit: bool) -> tuple[int, list[str]]:
    """Return the exit code and every line printed for ``gate``."""
    code, first = report(on, needed_groups(gate, devkit))
    out = [first, f"RUN: {command(gate)}", SWITCHED_ON]
    return code, out + ([PARTIAL] if GATES[gate][1] is not None else [])


def arguments(argv: Sequence[str]) -> str | None:
    """Return the gate ``--gate GATE`` names, or None if the arguments are not that."""
    if len(argv) == 2 and argv[0] == "--gate" and argv[1] in GATES:
        return argv[1]
    return None


def main(argv: Sequence[str]) -> int:
    """Print what ``--gate GATE`` needs and runs; return the exit code."""
    gate = arguments(argv)
    if gate is None:
        print(f"usage: groups.py --gate {{{'|'.join(GATES)}}}")
        return 2
    try:
        project = load_project()
        specs = import_optional_project_module(project.root, "specs")
    except ProjectError as err:
        print(f"no board project: {err.message}")
        return 2
    except Exception as err:  # the project's own code failed to import
        print(f"specs.py cannot be imported ({type(err).__name__}): fix it first")
        return 2
    devkit = hasattr(specs, "DEVKIT_REF")
    code, out = lines(gate, project.config.checks.groups, devkit)
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
