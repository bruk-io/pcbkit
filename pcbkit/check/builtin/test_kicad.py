"""KiCad's own checks, run fresh: electrical rules, design rules, schematic parity.

Switched on by ``kicad`` in ``[checks] groups``. They read nothing from ``specs.py``:
kicad-cli runs on the schematic and the board in the project's ``kicad/`` folder, and a
report that cannot be read fails the check instead of reading as clean.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from pcbkit.kicad import cli
from pcbkit.project import Project


def _scratch(name: str) -> Path:
    """Return a path for a report in a new temporary folder."""
    return Path(tempfile.mkdtemp(prefix="pcbkit_report_")) / name


def test_erc_clean(project: Project, record: Any) -> None:
    """Run ERC on the schematic with every severity on: no message at all."""
    schematic = project.kicad_dir / f"{project.config.board.stem}.kicad_sch"
    if not schematic.is_file():
        pytest.fail(f"{schematic} not found: run `pcbkit sch` first", pytrace=False)
    report = _scratch("erc.rpt")
    result = cli.erc(schematic, report, severity_all=True)
    record("erc", result.report.summary)
    assert result.report.messages == 0, report.read_text(encoding="utf-8")


def test_drc_clean_with_schematic_parity(project: Project, record: Any) -> None:
    """Run DRC with schematic parity: nothing found, of any kind."""
    pcb = project.kicad_dir / f"{project.config.board.stem}.kicad_pcb"
    if not pcb.is_file():
        pytest.fail(f"{pcb} not found: build and route the board first", pytrace=False)
    report = _scratch("drc.rpt")
    result = cli.drc(pcb, report, schematic_parity=True, severity_all=True)
    counts = result.report.counts
    record("drc", counts)
    assert counts and all(v == 0 for v in counts.values()), report.read_text(
        encoding="utf-8"
    )
