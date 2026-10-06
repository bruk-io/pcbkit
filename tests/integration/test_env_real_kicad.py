"""Integration: pcbkit.kicad.env against the real KiCad on this machine.

These tests need KiCad 10 installed, so they are marked ``kicad`` and are not part of
the CI run. Run them with ``uv run pytest tests/integration -m kicad``; leave them out
with ``-m "not kicad"``. They do not request the ``machine`` fixture, so they see the
real PATH, the real /Applications and the real HOME.
"""

from __future__ import annotations

import subprocess

import pytest

from pcbkit import doctor
from pcbkit.kicad import env

pytestmark = pytest.mark.kicad


def test_kicad_cli_is_found_and_runs() -> None:
    """Find kicad-cli, at a supported version, and have it answer for itself."""
    tool = env.find_kicad_cli()
    assert tool is not None, "kicad-cli not found: run `pcbkit doctor`"
    assert env.at_least(tool.version, env.MIN_KICAD)
    shown = subprocess.run(
        [tool.path, "--version"], capture_output=True, text=True, timeout=60
    )
    assert shown.returncode == 0
    assert shown.stdout.strip().startswith(tool.version)


def test_kicad_python_really_imports_pcbnew() -> None:
    """Probe KiCad's interpreter and then confirm it in a second, independent way."""
    found, reasons = env.probe_kicad_python()
    assert found is not None, reasons
    assert env.at_least(found.pcbnew_version, env.MIN_KICAD)
    check = subprocess.run(
        [found.path, "-I", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert check.returncode == 0
    assert found.pcbnew_version in check.stdout


def test_kicad_libraries_are_where_share_dir_says() -> None:
    """Find the stock libraries pcbkit builds boards from."""
    assert (env.symbols_dir() / "Device.kicad_sym").is_file()
    assert (env.footprints_dir() / "Resistor_SMD.pretty").is_dir()
    assert env.models_dir().is_dir()


def test_the_kicad_items_in_doctor_pass() -> None:
    """Pass doctor's four KiCad rows; the other tools are not KiCad's business."""
    kicad_rows = {"KiCad", "kicad-cli", "KiCad libraries", "KiCad Python"}
    checks = [c for c in doctor.diagnose() if c.name in kicad_rows]
    assert checks
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
