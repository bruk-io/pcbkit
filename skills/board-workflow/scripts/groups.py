#!/usr/bin/env python3
"""Say which of the check groups a review needs are switched off in pcbkit.toml.

``pcbkit check`` collects no built-in check of a group that ``[checks] groups`` leaves
out, so a ``-k`` selection over such a group runs nothing and decides nothing, without
a word. Each review names the groups its tables need; run this with those names, from
the project folder, under the project's own Python:

    .venv/bin/python "<plugin>/skills/board-workflow/scripts/groups.py" kicad circuit

It prints one line. Exit codes: 0 every group named is on, 1 at least one is off (the
line names them), 2 a name that is not a check group, or no board project here.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from pcbkit.project import CHECK_GROUPS, ProjectError, load_project


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


def main(argv: Sequence[str]) -> int:
    """Print which of the groups in ``argv`` are off; return the exit code."""
    try:
        project = load_project()
    except ProjectError as err:
        print(f"no board project: {err.message}")
        return 2
    code, line = report(project.config.checks.groups, argv)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
