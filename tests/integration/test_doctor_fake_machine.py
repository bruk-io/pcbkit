"""Integration: pcbkit doctor and pcbkit.kicad.env together, on a fully faked machine.

Only the machine is faked (see tests/fake_machine.py); the discovery code, the checks
and the report are the real ones. Unlike the unit tests, nothing here is stubbed
between doctor and env, so this is what catches the two modules drifting apart.
"""

from __future__ import annotations

import plistlib
import sys
import types
import zipfile

import pytest
from click.testing import CliRunner

from pcbkit import doctor
from pcbkit.cli import cli
from tests.fake_machine import FakeMachine


def install_fake_mac(machine: FakeMachine) -> None:
    """Install KiCad 10, Java 21, Freerouting and the other tools on a fake Mac."""
    machine.os_name = "macos"
    contents = machine.mac_app / "Contents"
    (contents / "SharedSupport" / "footprints").mkdir(parents=True)
    plist = {"CFBundleShortVersionString": "10.0.6"}
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    machine.exe(contents / "MacOS" / "kicad-cli", "10.0.6\n", on_path="kicad-cli")
    bundled = contents / "Frameworks" / "Python.framework" / "Versions" / "Current"
    machine.exe(bundled / "bin" / "python3", "pcbkit-probe 3.9.13 10.0.6\n")
    keg = machine.brew_arm / "opt" / "openjdk@21" / "bin" / "java"
    machine.exe(keg, 'openjdk version "21.0.11" 2026-10-20\n')
    jar = machine.home / ".local" / "share" / "pcbkit" / "freerouting-1.9.0.jar"
    jar.parent.mkdir(parents=True)
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    bin_dir = machine.root / "bin"
    machine.exe(
        bin_dir / "rsvg-convert",
        "rsvg-convert version 2.60.0\n",
        on_path="rsvg-convert",
    )
    machine.exe(bin_dir / "uv", "uv 0.12.15 (abc 2026-09-15)\n", on_path="uv")
    machine.exe(
        bin_dir / "ngspice",
        "** ngspice-47 : Circuit level simulation\n",
        on_path="ngspice",
    )


def with_pcbnew_here(monkeypatch: pytest.MonkeyPatch, importable: bool) -> None:
    """Make ``import pcbnew`` work, or fail, in this process."""
    fake = types.ModuleType("pcbnew") if importable else None
    monkeypatch.setitem(sys.modules, "pcbnew", fake)


def test_a_fully_installed_fake_mac_passes_every_check(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find every tool through the real discovery code and report all OK."""
    install_fake_mac(machine)
    with_pcbnew_here(monkeypatch, importable=True)
    checks = doctor.diagnose()
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert [c.name for c in checks][:5] == [
        "KiCad",
        "kicad-cli",
        "KiCad libraries",
        "KiCad Python",
        "Java",
    ]
    assert doctor.exit_code(checks) == 0


def test_doctor_runs_exactly_the_commands_it_needs(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run one version query per tool and one pcbnew probe, and nothing else."""
    install_fake_mac(machine)
    with_pcbnew_here(monkeypatch, importable=True)
    doctor.diagnose()
    executables = [call[0].rsplit("/", 1)[-1] for call in machine.calls]
    assert executables == [
        "kicad-cli",
        "python3",
        "java",
        "rsvg-convert",
        "uv",
        "ngspice",
    ]


def test_a_fake_mac_with_nothing_installed_fails_with_mac_fixes(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report every required item MISSING with its Homebrew fix, running no command."""
    machine.os_name = "macos"
    with_pcbnew_here(monkeypatch, importable=False)
    checks = doctor.diagnose()
    assert [c.name for c in checks if c.required and not c.ok] == [
        "KiCad",
        "kicad-cli",
        "KiCad libraries",
        "KiCad Python",
        "Java",
        "Freerouting",
        "rsvg-convert",
        "uv",
    ]
    assert doctor.exit_code(checks) == 1
    fixes = {c.name: c.fix for c in checks}
    assert fixes["KiCad"] == "brew install --cask kicad"
    assert fixes["Java"].startswith("brew install openjdk@21")
    assert fixes["rsvg-convert"] == "brew install librsvg"
    assert fixes["uv"] == "brew install uv"
    assert machine.calls == []


def test_a_fake_mac_whose_kicad_python_cannot_import_pcbnew(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the interpreter's own error as the reason."""
    install_fake_mac(machine)
    with_pcbnew_here(monkeypatch, importable=True)
    bundled = machine.mac_app / "Contents" / "Frameworks" / "Python.framework"
    python = bundled / "Versions" / "Current" / "bin" / "python3"
    machine.exe(
        python,
        "Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'pcbnew'\n",
        returncode=1,
    )
    found = {c.name: c for c in doctor.diagnose()}["KiCad Python"]
    assert not found.ok
    assert f"{python}: ModuleNotFoundError: No module named 'pcbnew'" in found.detail


def test_a_fake_linux_box_uses_system_python_and_apt_fixes(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find KiCad through /usr, and offer apt where something is missing."""
    machine.os_name = "linux"
    (machine.linux_share / "footprints").mkdir(parents=True)
    machine.exe(machine.usr_bin / "kicad-cli", "10.0.6\n")
    machine.exe(
        machine.usr_bin / "python3", "pcbkit-probe 3.12.3 10.0.6\n", on_path="python3"
    )
    with_pcbnew_here(monkeypatch, importable=False)
    checks = {c.name: c for c in doctor.diagnose()}
    assert "KiCad" not in checks
    assert checks["kicad-cli"].ok
    assert checks["KiCad libraries"].ok
    assert checks["KiCad Python"].detail.startswith("Python 3.12.3, pcbnew 10.0.6")
    assert checks["Java"].fix == "sudo apt install openjdk-21-jre"
    assert checks["rsvg-convert"].fix == "sudo apt install librsvg2-bin"
    assert checks["ngspice"].fix == "sudo apt install ngspice"


def test_the_doctor_command_end_to_end_on_a_fake_mac(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run `pcbkit doctor` through the CLI and read its report."""
    install_fake_mac(machine)
    with_pcbnew_here(monkeypatch, importable=True)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[1].startswith("OK       KiCad ")
    assert lines[-1] == "All required items are present."
    assert "MISSING" not in result.output


def test_the_doctor_command_exits_1_when_something_required_is_missing(
    machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail the command, naming the missing item and the way to fix it."""
    install_fake_mac(machine)
    with_pcbnew_here(monkeypatch, importable=True)
    machine.path_tools.pop("uv")
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert "MISSING  uv" in result.output
    assert "(fix: brew install uv)" in result.output
    assert result.output.splitlines()[-1] == "1 required item missing: uv."
