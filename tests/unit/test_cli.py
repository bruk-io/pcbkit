"""Unit tests for pcbkit.cli: the command surface, the stubs, and the doctor command."""

from __future__ import annotations

import platform

import pytest
from click.testing import CliRunner, Result

from pcbkit import __version__, doctor
from pcbkit.cli import cli
from pcbkit.doctor import Check

# The expected order of `pcbkit --help`: the order the commands are used in.
WORKFLOW = [
    "new",
    "doctor",
    "setup",
    "datasheet",
    "sch",
    "build",
    "route",
    "promote",
    "finalize",
    "check",
    "mutants",
    "report",
    "quote",
    "compare",
    "shots",
]

# Command -> (work package that implements it, argv that reaches the stub). This table
# is the record of who fills in what. Every command is built now, so it is empty: a
# new command starts as a stub with a row here until it has its own tests.
BUILT = {
    "new",
    "setup",
    "datasheet",
    "doctor",
    "sch",
    "build",
    "quote",
    "route",
    "promote",
    "finalize",
    "compare",
    "shots",
    "check",
    "mutants",
    "report",
}
STUBS: dict[str, tuple[str, list[str]]] = {}


def invoke(*args: str) -> Result:
    """Run the pcbkit CLI in-process and return the result."""
    return CliRunner().invoke(cli, list(args))


def listed_commands(help_text: str) -> list[str]:
    """Return the command names under "Commands:" in a --help text, in order."""
    lines = help_text.split("Commands:\n", 1)[1].splitlines()
    return [line.split()[0] for line in lines if line.strip()]


# --- the command surface ----------------------------------------------------------


def test_help_lists_every_command_in_workflow_order() -> None:
    """Show new, doctor, setup, ... shots in the order they are used."""
    result = invoke("--help")
    assert result.exit_code == 0
    assert listed_commands(result.output) == WORKFLOW
    assert list(cli.commands) == WORKFLOW


def test_no_command_summary_is_cut_off_in_help() -> None:
    """Keep each first line short enough that click does not end it with "..."."""
    commands = invoke("--help").output.split("Commands:\n", 1)[1]
    assert "..." not in commands


def test_short_help_flag_works_too() -> None:
    """Accept -h as well as --help."""
    assert invoke("-h").output == invoke("--help").output


def test_version_option_prints_the_package_version() -> None:
    """Print "pcbkit, version X"."""
    result = invoke("--version")
    assert result.exit_code == 0
    assert result.output.strip() == f"pcbkit, version {__version__}"


def test_every_command_has_help_that_names_what_it_does() -> None:
    """Give each command a one-line summary in --help."""
    for name in WORKFLOW:
        result = invoke(name, "--help")
        assert result.exit_code == 0, name
        assert f"{name} [OPTIONS]" in result.output.splitlines()[0], name
        assert len(result.output.splitlines()) > 3, name


# --- the stubs --------------------------------------------------------------------


def test_every_command_that_is_not_built_has_a_stub_test() -> None:
    """Fail when a command is added without a row in STUBS."""
    assert set(cli.commands) - BUILT == set(STUBS)


@pytest.mark.parametrize(
    ("name", "options"),
    [
        ("new", ["--from", "[default: blinky]", "--pcbkit-source", "PCBKIT_SOURCE"]),
        ("route", ["--eco", "--tries", "--passes"]),
        ("finalize", ["--no-render"]),
        ("check", ["-k"]),
        ("datasheet", ["find", "confirm"]),
        ("quote", ["--assembled", "--fab-qty", "--self-solder-tht", "--notes"]),
    ],
)
def test_help_shows_each_option(name: str, options: list[str]) -> None:
    """List each option in the command's --help."""
    output = invoke(name, "--help").output
    for option in options:
        assert option in output, (name, option)


@pytest.mark.parametrize(
    "argv",
    [
        ["route", "--tries", "0"],
        ["route", "--passes", "x"],
        ["quote", "--assembled", "0"],
        ["quote", "--fab-qty", "-3"],
        ["compare", "only-one.kicad_pcb"],
        ["new"],
        ["route", "--no-such-option"],
        ["no-such-command"],
    ],
)
def test_bad_arguments_are_usage_errors_not_stub_messages(argv: list[str]) -> None:
    """Reject bad values and missing arguments with click's usage error (exit 2)."""
    result = invoke(*argv)
    assert result.exit_code == 2
    assert "not implemented yet" not in result.output


# --- doctor -----------------------------------------------------------------------

GOOD = Check("KiCad", True, "10.0.6  /Apps/KiCad.app")
MISSING_REQUIRED = Check("Java", False, "not found", "brew install openjdk@21")
MISSING_OPTIONAL = Check("ngspice", False, "not found", "brew install ngspice", False)


def stub_diagnose(monkeypatch: pytest.MonkeyPatch, checks: list[Check]) -> None:
    """Make doctor.diagnose return ``checks``."""
    monkeypatch.setattr(doctor, "diagnose", lambda: checks)


def test_doctor_with_everything_present_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print each item and exit 0."""
    stub_diagnose(monkeypatch, [GOOD])
    result = invoke("doctor")
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[1] == "OK       KiCad  10.0.6  /Apps/KiCad.app"
    assert lines[-1] == "All required items are present."


def test_doctor_header_names_version_os_and_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Say which pcbkit, OS and Python produced the report."""
    stub_diagnose(monkeypatch, [GOOD])
    header = invoke("doctor").output.splitlines()[0]
    assert header.startswith(f"pcbkit {__version__}: ")
    assert header.endswith(f"Python {platform.python_version()}")


def test_doctor_with_a_required_item_missing_exits_1_and_says_how_to_fix_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Show the MISSING line with its fix and exit 1."""
    stub_diagnose(monkeypatch, [GOOD, MISSING_REQUIRED])
    result = invoke("doctor")
    assert result.exit_code == 1
    assert "MISSING  Java   not found  (fix: brew install openjdk@21)" in result.output
    assert result.output.splitlines()[-1] == "1 required item missing: Java."


def test_doctor_with_only_an_optional_item_missing_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still report the optional item, with its fix, but exit 0."""
    stub_diagnose(monkeypatch, [GOOD, MISSING_OPTIONAL])
    result = invoke("doctor")
    assert result.exit_code == 0
    assert "MISSING  ngspice (optional)  not found" in result.output
    assert "brew install ngspice" in result.output


def test_doctor_prints_no_colour_codes_when_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strip the ANSI styling when output is captured."""
    stub_diagnose(monkeypatch, [GOOD, MISSING_REQUIRED])
    assert "\x1b" not in invoke("doctor").output
