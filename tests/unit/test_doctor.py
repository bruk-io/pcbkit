"""Unit tests for pcbkit.doctor, with every env finder replaced by a stub."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit import doctor
from pcbkit.doctor import Check
from pcbkit.kicad import env
from pcbkit.kicad.env import KicadPython, Tool
from tests.fake_machine import FakeMachine

HEALTHY: dict[str, Any] = {
    "find_kicad_app": Tool("/Apps/KiCad.app", "10.0.6"),
    "find_kicad_cli": Tool("/bin/kicad-cli", "10.0.6"),
    "find_share": Path("/share/kicad"),
    "probe_kicad_python": (KicadPython("/kpy", "3.9.13", "10.0.6"), []),
    "find_java": Tool("/jdk/bin/java", "21.0.11"),
    "find_freerouting_jar": Tool("/jars/freerouting-1.9.0.jar", "1.9.0"),
    "is_jar": True,
    "find_rsvg_convert": Tool("/bin/rsvg-convert", "2.60.0"),
    "find_uv": Tool("/bin/uv", "0.12.15"),
    "find_ngspice": Tool("/bin/ngspice", "47"),
    "find_pdftotext": Tool("/bin/pdftotext", "26.02.0"),
    "pcbnew_importable": True,
}

MAC_ROWS = [
    "KiCad",
    "kicad-cli",
    "KiCad libraries",
    "KiCad Python",
    "Java",
    "Freerouting",
    "rsvg-convert",
    "uv",
    "ngspice",
    "pdftotext",
    "pcbnew here",
]


def stub(monkeypatch: pytest.MonkeyPatch, **returns: Any) -> None:
    """Make each named env function return the given value, whatever its arguments."""
    for name, value in returns.items():
        monkeypatch.setattr(env, name, lambda *args, _v=value, **kw: _v)


@pytest.fixture
def healthy_mac(monkeypatch: pytest.MonkeyPatch, machine: FakeMachine) -> None:
    """Make every finder report a healthy macOS machine."""
    machine.os_name = "macos"
    stub(monkeypatch, **HEALTHY)


def rows(checks: list[Check]) -> dict[str, Check]:
    """Index a list of checks by name."""
    return {check.name: check for check in checks}


# --- diagnose ---------------------------------------------------------------------


def test_a_healthy_mac_is_all_ok_in_report_order(healthy_mac: None) -> None:
    """List every item, in order, all OK, with exit status 0."""
    checks = doctor.diagnose()
    assert [c.name for c in checks] == MAC_ROWS
    assert all(c.ok for c in checks)
    assert doctor.exit_code(checks) == 0
    found = rows(checks)
    assert found["KiCad"].detail == "10.0.6  /Apps/KiCad.app"
    assert found["KiCad libraries"].detail == "/share/kicad"
    assert found["KiCad Python"].detail == "Python 3.9.13, pcbnew 10.0.6  /kpy"
    assert found["Java"].detail == "21.0.11  /jdk/bin/java"


def test_only_ngspice_pdftotext_and_pcbnew_here_are_optional(
    healthy_mac: None,
) -> None:
    """Document which items do not decide the exit status."""
    optional = [c.name for c in doctor.diagnose() if not c.required]
    assert optional == ["ngspice", "pdftotext", "pcbnew here"]


def test_linux_has_no_kicad_app_row(healthy_mac: None, machine: FakeMachine) -> None:
    """Skip the KiCad.app row where there is no app bundle."""
    machine.os_name = "linux"
    assert [c.name for c in doctor.diagnose()] == MAC_ROWS[1:]


MISSING = [
    # (finder, value, row, text in the detail, text in the fix)
    ("find_kicad_app", None, "KiCad", "not found at ", "brew install --cask kicad"),
    (
        "find_kicad_cli",
        None,
        "kicad-cli",
        "not found on PATH",
        "brew install --cask kicad",
    ),
    ("find_share", None, "KiCad libraries", "no footprints/", "set KICAD_SHARE"),
    (
        "probe_kicad_python",
        (None, []),
        "KiCad Python",
        "none found",
        "brew install --cask kicad",
    ),
    (
        "probe_kicad_python",
        (None, ["/kpy: ModuleNotFoundError: No module named 'pcbnew'"]),
        "KiCad Python",
        "/kpy: ModuleNotFoundError: No module named 'pcbnew'",
        "brew install --cask kicad",
    ),
    ("find_java", None, "Java", "needs Java 17 or newer", "brew install openjdk@21"),
    (
        "find_freerouting_jar",
        None,
        "Freerouting",
        "freerouting-1.9.0.jar is not in ~/.local/share/pcbkit",
        "run `pcbkit setup` in a board project",
    ),
    (
        "find_rsvg_convert",
        None,
        "rsvg-convert",
        "finalize and shots draw PNGs",
        "brew install librsvg",
    ),
    ("find_uv", None, "uv", "pcbkit projects use it", "brew install uv"),
]


@pytest.mark.parametrize(("finder", "value", "row", "detail", "fix"), MISSING)
def test_a_missing_required_item_fails_the_run_with_a_fix(
    healthy_mac: None,
    monkeypatch: pytest.MonkeyPatch,
    finder: str,
    value: Any,
    row: str,
    detail: str,
    fix: str,
) -> None:
    """Mark exactly that row MISSING and required, say why, and exit 1."""
    stub(monkeypatch, **{finder: value})
    checks = doctor.diagnose()
    found = rows(checks)
    assert not found[row].ok
    assert found[row].required
    assert detail in found[row].detail
    assert fix in found[row].fix
    assert [c.name for c in checks if not c.ok] == [row]
    assert doctor.exit_code(checks) == 1


def test_a_missing_ngspice_is_reported_but_does_not_fail_the_run(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the fix for ngspice and still exit 0."""
    stub(monkeypatch, find_ngspice=None)
    checks = doctor.diagnose()
    ngspice = rows(checks)["ngspice"]
    assert not ngspice.ok
    assert not ngspice.required
    assert "only checks that run SPICE need it" in ngspice.detail
    assert ngspice.fix == "brew install ngspice"
    assert doctor.exit_code(checks) == 0


def test_a_missing_pdftotext_is_reported_but_does_not_fail_the_run(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the fix for pdftotext and still exit 0: only pcbkit datasheet needs it."""
    stub(monkeypatch, find_pdftotext=None)
    checks = doctor.diagnose()
    pdftotext = rows(checks)["pdftotext"]
    assert not pdftotext.ok and not pdftotext.required
    assert "only pcbkit datasheet reads PDFs" in pdftotext.detail
    assert pdftotext.fix == "brew install poppler"
    assert doctor.exit_code(checks) == 0


def test_pcbnew_missing_in_this_python_is_reported_but_optional(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point at `pcbkit setup` without failing a tier 1 environment."""
    stub(monkeypatch, pcbnew_importable=False)
    checks = doctor.diagnose()
    here = rows(checks)["pcbnew here"]
    assert not here.ok
    assert not here.required
    assert "tier 2 commands" in here.detail
    assert "pcbkit setup" in here.fix
    assert doctor.exit_code(checks) == 0


# --- version floors ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("finder", "row", "old"),
    [
        ("find_kicad_app", "KiCad", Tool("/Apps/KiCad.app", "9.0.9")),
        ("find_kicad_cli", "kicad-cli", Tool("/bin/kicad-cli", "9.0.9")),
    ],
)
def test_kicad_older_than_10_fails(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch, finder: str, row: str, old: Tool
) -> None:
    """Fail KiCad 9, saying what was found and what is needed."""
    stub(monkeypatch, **{finder: old})
    found = rows(doctor.diagnose())[row]
    assert not found.ok
    assert found.detail == f"found 9.0.9 at {old.path}; pcbkit needs 10.0"
    assert "brew install --cask kicad" in found.fix


def test_pcbnew_older_than_10_fails(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail KiCad's Python when its pcbnew is version 9."""
    stub(monkeypatch, probe_kicad_python=(KicadPython("/kpy", "3.9.13", "9.0.9"), []))
    found = rows(doctor.diagnose())["KiCad Python"]
    assert not found.ok
    assert found.detail == "pcbnew 9.0.9 at /kpy; pcbkit needs 10.0"


@pytest.mark.parametrize("version", ["10.0.0", "10.0.6", "11.1.0", ""])
def test_kicad_at_the_floor_or_with_unknown_version_passes(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Accept 10.0.0 and newer, and a version that cannot be read."""
    stub(monkeypatch, find_kicad_app=Tool("/Apps/KiCad.app", version))
    assert rows(doctor.diagnose())["KiCad"].ok


def test_an_unknown_kicad_version_is_shown_as_unknown(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say "version unknown" rather than print an empty column."""
    stub(monkeypatch, find_kicad_app=Tool("/Apps/KiCad.app", ""))
    assert rows(doctor.diagnose())["KiCad"].detail == "version unknown  /Apps/KiCad.app"


@pytest.mark.parametrize("version", ["11.0.2", "1.8.0_292", "9", "abc"])
def test_java_older_than_17_or_unreadable_fails(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Fail an old Java with what was found and what is needed."""
    stub(monkeypatch, find_java=Tool("/usr/bin/java", version))
    found = rows(doctor.diagnose())["Java"]
    assert not found.ok
    assert found.detail == f"found {version} at /usr/bin/java; need 17 or newer"
    assert "brew install openjdk@21" in found.fix


@pytest.mark.parametrize("version", ["17", "17.0.20.1", "21.0.11", "21-ea"])
def test_java_17_or_newer_passes(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Accept Java 17 and up."""
    stub(monkeypatch, find_java=Tool("/usr/bin/java", version))
    assert rows(doctor.diagnose())["Java"].ok


# --- Freerouting and the libraries ------------------------------------------------


def test_a_missing_freerouting_override_is_named(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say that FREEROUTING_JAR points at nothing, not that the jar was not found."""
    monkeypatch.setenv("FREEROUTING_JAR", "/x/f.jar")
    stub(monkeypatch, find_freerouting_jar=None)
    found = rows(doctor.diagnose())["Freerouting"]
    assert found.detail == "FREEROUTING_JAR=/x/f.jar does not exist"


def test_a_truncated_freerouting_jar_fails(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a jar that is not a valid zip file."""
    stub(monkeypatch, is_jar=False)
    found = rows(doctor.diagnose())["Freerouting"]
    assert not found.ok
    assert found.detail == (
        "/jars/freerouting-1.9.0.jar is not a valid jar (a truncated download?)"
    )
    assert "pcbkit setup" in found.fix


def test_a_wrong_kicad_share_override_is_named(
    healthy_mac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Say that KICAD_SHARE has no footprints/ folder."""
    monkeypatch.setenv("KICAD_SHARE", "/bad/share")
    stub(monkeypatch, find_share=None)
    found = rows(doctor.diagnose())["KiCad libraries"]
    assert found.detail == "KICAD_SHARE=/bad/share has no footprints/ folder"


# --- fix hints by OS --------------------------------------------------------------


@pytest.mark.parametrize(
    ("os_name", "finder", "row", "fix"),
    [
        ("linux", "find_java", "Java", "sudo apt install openjdk-21-jre"),
        ("linux", "find_rsvg_convert", "rsvg-convert", "sudo apt install librsvg2-bin"),
        ("linux", "find_ngspice", "ngspice", "sudo apt install ngspice"),
        (
            "linux",
            "find_uv",
            "uv",
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
        ),
        (
            "linux",
            "find_kicad_cli",
            "kicad-cli",
            "install KiCad 10 from https://www.kicad.org/download/",
        ),
        ("win32", "find_java", "Java", "install a JDK or JRE, version 17 or newer"),
        ("win32", "find_ngspice", "ngspice", "install ngspice"),
        ("macos", "find_ngspice", "ngspice", "brew install ngspice"),
    ],
)
def test_fix_hints_follow_the_os(
    healthy_mac: None,
    machine: FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    os_name: str,
    finder: str,
    row: str,
    fix: str,
) -> None:
    """Offer apt on Linux, Homebrew on macOS and a plain instruction elsewhere."""
    machine.os_name = os_name
    stub(monkeypatch, **{finder: None})
    assert rows(doctor.diagnose())[row].fix == fix


def test_every_fix_has_an_entry_for_other_systems() -> None:
    """Never leave an OS without a hint: each item needs an "other" entry."""
    for item, options in doctor.FIXES.items():
        assert "other" in options, item


# --- the report -------------------------------------------------------------------

SAMPLE = [
    Check("KiCad", True, "10.0.6  /Apps/KiCad.app"),
    Check("Java", False, "not found", "brew install openjdk@21"),
    Check("ngspice", False, "not found", "brew install ngspice", required=False),
]


def test_report_has_one_aligned_line_per_check_and_a_summary() -> None:
    """Print status, name, detail and, for a miss, the fix."""
    assert doctor.format_report(SAMPLE).splitlines() == [
        "OK       KiCad               10.0.6  /Apps/KiCad.app",
        "MISSING  Java                not found  (fix: brew install openjdk@21)",
        "MISSING  ngspice (optional)  not found  (fix: brew install ngspice)",
        "1 required item missing: Java. Optional, not found: ngspice.",
    ]


def test_report_summary_for_a_clean_run() -> None:
    """Say so when nothing required is missing."""
    ok = [Check("KiCad", True, "10.0.6  /x"), Check("uv", True, "0.1  /y")]
    assert doctor.format_report(ok).splitlines()[-1] == (
        "All required items are present."
    )


def test_report_summary_pluralises_and_lists_every_missing_item() -> None:
    """List each missing required item by name."""
    bad = [Check("Java", False, "x", "fix a"), Check("uv", False, "y", "fix b")]
    assert doctor.format_report(bad).splitlines()[-1] == (
        "2 required items missing: Java, uv."
    )


def test_report_leaves_out_an_empty_fix() -> None:
    """Print no "(fix: )" for a missing item that has no hint."""
    line = doctor.format_report([Check("X", False, "gone")]).splitlines()[0]
    assert line == "MISSING  X  gone"


def test_report_of_nothing_is_just_the_summary() -> None:
    """Not fail on an empty list."""
    assert doctor.format_report([]) == "All required items are present."
    assert doctor.exit_code([]) == 0


def test_report_colour_only_styles_the_status() -> None:
    """Add terminal colour without changing the text."""
    plain = doctor.format_report(SAMPLE)
    coloured = doctor.format_report(SAMPLE, color=True)
    assert "\x1b[" in coloured
    assert click.unstyle(coloured) == plain


@pytest.mark.parametrize(
    ("checks", "expected"),
    [
        ([], 0),
        ([Check("a", True, "")], 0),
        ([Check("a", False, "", required=False)], 0),
        ([Check("a", False, "")], 1),
        ([Check("a", True, ""), Check("b", False, "")], 1),
        ([Check("a", False, "", required=False), Check("b", False, "")], 1),
    ],
)
def test_exit_code_depends_only_on_required_items(
    checks: list[Check], expected: int
) -> None:
    """Exit 1 exactly when a required item is not OK."""
    assert doctor.exit_code(checks) == expected
