"""Where the results of a check run are kept, and how they are laid out.

Kept apart from the plugin (which needs pytest) so that ``pcbkit report`` and anything
else that only reads the results can import this without pytest.

``out/checks/results.json`` holds, for the last ``pcbkit check`` run::

    {"format": 1, "board": {...}, "copper_mm": 0.035, "groups": [...],
     "exit_status": 1, "counts": {"passed": 3, ...}, "seconds": 12.3,
     "selection": {"keyword": "", "markexpr": "", "deselected": 0, "complete": true},
     "checks": {"<check id>": {"group": "kicad" or "project", "outcome": "passed",
                               "numbers": {...}, "reason": "...", "message": "..."}}}

A check's id is ``pcbkit.check.builtin.<module>::<name>[param]`` for a built-in check
and ``<path relative to the project>::<name>[param]`` for the project's own. ``reason``
is only there for a skipped check and ``message`` for a failed or errored one.
``selection`` says whether the run was cut short: ``complete`` is false after a ``-k``
or ``-m`` run, or any run that deselected checks, so a gate can refuse partial results.
"""

from __future__ import annotations

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


def results_dir(project: Project) -> Path:
    """Return the folder the results, the report and the plots go in."""
    return project.out_dir / RESULTS_DIR
