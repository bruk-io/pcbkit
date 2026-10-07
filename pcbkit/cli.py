"""The pcbkit command line.

One function per command, in workflow order. A command that is not built yet is a stub
that fails with "not implemented yet (WPn)", naming the work package that fills it in.

Command functions are called ``<command>_cmd`` and registered under an explicit name, so
a command named ``check`` or ``report`` never shadows the module of the same name that
the work package implementing it will import here.

Tier 2 commands (build, route, promote, finalize, check, mutants, compare, shots) need
pcbnew. Whoever implements one must call ``pcbkit.kicad.env.require_pcbnew()`` first, so
a missing pcbnew is a message rather than a traceback. The stubs do not, on purpose:
they say "not implemented yet" wherever they run.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import NoReturn

import click

from pcbkit import __version__, doctor, sch
from pcbkit.kicad import env
from pcbkit.project import load_project


class _WorkflowGroup(click.Group):
    """A command group that lists its commands in workflow order, not alphabetically."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


def _not_implemented(wp: str) -> NoReturn:
    """Fail with the message every stub shares."""
    raise click.ClickException(f"not implemented yet ({wp})")


@click.group(
    cls=_WorkflowGroup, context_settings={"help_option_names": ["-h", "--help"]}
)
@click.version_option(package_name="pcbkit", prog_name="pcbkit")
def cli() -> None:
    """Design, route, check and export KiCad boards from Python.

    Run the commands inside a board project: a folder with a pcbkit.toml. They are
    listed in the order you use them. Commands that need KiCad's pcbnew module (build,
    route, promote, finalize, check, mutants, compare, shots) run in the project's own
    .venv, which `pcbkit setup` creates; the rest run anywhere.
    """


@cli.command("new")
@click.argument("name")
@click.option(
    "--from",
    "template",
    default="blinky",
    show_default=True,
    help="The example board to start from.",
)
def new_cmd(name: str, template: str) -> None:
    """Create a board project called NAME from a template.

    Not implemented yet (WP11).
    """
    _not_implemented("WP11")


@cli.command("doctor")
def doctor_cmd() -> None:
    """Check this machine has everything pcbkit needs.

    Looks for KiCad, Java, Freerouting and the other tools. Prints one line per item:
    OK or MISSING, the version and path found, and a fix for anything that is missing.
    Exits 1 if a required item is missing. ngspice and pcbnew in this Python are
    optional.
    """
    checks = doctor.diagnose()
    click.echo(
        f"pcbkit {__version__}: {env.host_os()}, Python {platform.python_version()}"
    )
    click.echo(doctor.format_report(checks, color=True))
    code = doctor.exit_code(checks)
    if code:
        click.get_current_context().exit(code)


@cli.command("setup")
def setup_cmd() -> None:
    """Set up the project .venv and fetch Freerouting.

    The .venv is built on KiCad's own Python, so that pcbnew imports in it.

    Not implemented yet (WP11).
    """
    _not_implemented("WP11")


@cli.command("sch")
def sch_cmd() -> None:
    """Generate the schematic, run ERC and export the netlist.

    Builds the project's own symbol and footprint libraries, draws the schematic from
    design.py, checks it with KiCad's ERC and exports the netlist, all into kicad/.
    Needs kicad-cli but not pcbnew. Exits 1 if ERC reports errors; warnings are listed
    and do not fail it.
    """
    proj = load_project()
    result = sch.build_schematic(proj)
    click.echo(sch.format_result(result, proj.root))
    if result.erc.errors:
        click.get_current_context().exit(1)


@cli.command("build")
def build_cmd() -> None:
    """Generate the schematic and netlist, then place the board.

    Not implemented yet (WP4).
    """
    _not_implemented("WP4")


@cli.command("route")
@click.option(
    "--eco",
    type=click.Path(file_okay=False, path_type=Path),
    metavar="PATH",
    help="Keep the route in this golden-style folder and only route what changed.",
)
@click.option(
    "--tries",
    type=click.IntRange(min=1),
    metavar="N",
    help="Freerouting runs to try before giving up (default: [route] tries).",
)
@click.option(
    "--passes",
    type=click.IntRange(min=1),
    metavar="N",
    help="Freerouting passes per run (default: [route] freerouting_passes).",
)
def route_cmd(eco: Path | None, tries: int | None, passes: int | None) -> None:
    """Pre-route, run Freerouting, fill zones and run DRC.

    Not implemented yet (WP5).
    """
    _not_implemented("WP5")


@cli.command("promote")
def promote_cmd() -> None:
    """Save the routed board as the golden route.

    Not implemented yet (WP5).
    """
    _not_implemented("WP5")


@cli.command("finalize")
@click.option(
    "--no-render", is_flag=True, help="Skip the 3D renders, which take the longest."
)
def finalize_cmd(no_render: bool) -> None:
    """Rebuild from the golden route, run DRC and export the fab files.

    Not implemented yet (WP5).
    """
    _not_implemented("WP5")


@cli.command("check")
@click.option(
    "-k",
    "expression",
    metavar="EXPR",
    help="Only run the checks that match this pytest -k expression.",
)
def check_cmd(expression: str | None) -> None:
    """Run the design checks against the board.

    Runs the built-in checks of the groups listed in [checks] groups, then the
    project's own checks/ folder, with pytest. Writes out/checks/results.json and exits
    1 if any check fails. Needs pcbnew.
    """
    env.require_pcbnew()
    proj = load_project()
    from pcbkit.check import plugin, runner

    code = runner.run(proj, expression)
    results = plugin.results_dir(proj) / plugin.RESULTS_FILE
    if results.is_file():
        click.echo(f"Results: {results.relative_to(proj.root)} (pcbkit report)")
    click.get_current_context().exit(code)


@cli.command("mutants")
def mutants_cmd() -> None:
    """Plant known mistakes and confirm the checks catch every one.

    Reads MUTANTS from the project's mutants.py. Each is planted in design.py in a
    scratch copy of the project and the checks it names must then fail; a control run on
    an unedited copy must pass first. Exits 1 if a mistake is missed, 2 if the control
    fails. Needs pcbnew.
    """
    env.require_pcbnew()
    proj = load_project()
    from pcbkit import mutants

    click.get_current_context().exit(mutants.run_all(proj))


@cli.command("report")
def report_cmd() -> None:
    """Write the validation report from the last check run.

    Not implemented yet (WP7).
    """
    _not_implemented("WP7")


@cli.command("quote")
@click.option(
    "--assembled",
    type=click.IntRange(min=1),
    metavar="N",
    help="Number of boards to have assembled.",
)
@click.option(
    "--fab-qty",
    type=click.IntRange(min=1),
    metavar="N",
    help="Number of bare boards to have made.",
)
@click.option(
    "--self-solder-tht",
    is_flag=True,
    help="Leave the through-hole parts to solder yourself.",
)
@click.option(
    "--notes",
    type=click.Path(dir_okay=False, path_type=Path),
    metavar="FILE",
    help="A text file of order notes; the form allows 600 characters.",
)
def quote_cmd(
    assembled: int | None,
    fab_qty: int | None,
    self_solder_tht: bool,
    notes: Path | None,
) -> None:
    """Print every number the PCBWay quote form asks for.

    Not implemented yet (WP6).
    """
    _not_implemented("WP6")


@cli.command("compare")
@click.argument("old", type=click.Path(path_type=Path))
@click.argument("new", type=click.Path(path_type=Path))
def compare_cmd(old: Path, new: Path) -> None:
    """Compare the copper of two boards; exit 1 if they differ.

    Not implemented yet (WP8).
    """
    _not_implemented("WP8")


@cli.command("shots")
def shots_cmd() -> None:
    """Export crops and renders of named board regions for review.

    Not implemented yet (WP8).
    """
    _not_implemented("WP8")
