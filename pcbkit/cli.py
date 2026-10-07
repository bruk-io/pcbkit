"""The pcbkit command line.

One function per command, in workflow order. A command that is not built yet is a stub
that fails with "not implemented yet (WPn)", naming the work package that fills it in.

Command functions are called ``<command>_cmd`` and registered under an explicit name, so
a command named ``check`` or ``report`` never shadows the module of the same name that
the work package implementing it will import here.

Tier 2 commands (build, route, promote, finalize, check, mutants, compare) need
pcbnew. Whoever implements one must call ``pcbkit.kicad.env.require_pcbnew()`` first, so
a missing pcbnew is a message rather than a traceback. The stubs do not, on purpose:
they say "not implemented yet" wherever they run. ``shots`` is tier 1: it needs only
kicad-cli and rsvg-convert.
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
@click.option(
    "--pcbkit-source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    envvar="PCBKIT_SOURCE",
    metavar="PATH",
    help="A pcbkit checkout for the project to install pcbkit from, instead of its "
    "git repository (default: the git repository). Also read from PCBKIT_SOURCE.",
)
def new_cmd(name: str, template: str, pcbkit_source: Path | None) -> None:
    """Create a board project called NAME from a template.

    Copies the template into a new folder NAME (it may be a path; the folder must not
    exist, or must be empty) and names the board after it: `my-board` becomes the file
    stem my_board, the title My Board and the fab files My_Board_revA. The template is
    the blinky example: a power connector, a resistor and an LED on a 30 x 20 mm
    two-layer board, with its checks and a route that passed DRC, so it builds as it is.
    Also written: the project's pyproject.toml, which depends on pcbkit and pytest, and
    a README.md with the commands. Then cd into the folder and run `pcbkit setup`.

    The project installs pcbkit from its git repository. To install it from your own
    copy instead, give the folder you cloned it into with --pcbkit-source (or set
    PCBKIT_SOURCE): the project's pyproject.toml then names it as an editable path
    source.
    """
    from pcbkit import scaffold

    if template not in scaffold.TEMPLATES:
        known = ", ".join(sorted(scaffold.TEMPLATES))
        raise click.BadParameter(
            f"{template!r} is not a template: choose from {known}", param_hint="--from"
        )
    created = scaffold.create_project(name, template, pcbkit_source)
    click.echo(scaffold.format_result(created))


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

    Run it in a board project (a folder with a pcbkit.toml and a pyproject.toml): once
    after `pcbkit new`, and again whenever the dependencies change. It builds .venv on
    KiCad's own Python, so that pcbnew imports in it (`uv venv --system-site-packages`),
    installs the project's dependencies into it (`uv sync`) and checks that
    `import pcbnew` works there. A .venv that already does is kept. It then makes sure
    the Freerouting 1.9.0 jar is there: it uses one it finds (FREEROUTING_JAR,
    ~/.local/share/pcbkit or ~/.local/share/freerouting), and otherwise downloads it
    from Freerouting's GitHub release into ~/.local/share/pcbkit. Last it prints the
    commands to run next, from .venv/bin.

    Needs uv and KiCad 10; a failure says what is wrong and how to fix it, and
    `pcbkit doctor` shows what is missing on the machine. It does not need pcbnew in
    the Python that runs it, so it works from `uvx` or any environment with pcbkit.
    """
    from pcbkit import bootstrap

    proj = load_project()
    click.echo(f"pcbkit setup: {proj.config.board.title} in {proj.root}")
    result = bootstrap.setup_project(proj.root)
    click.echo(bootstrap.format_next(result))


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

    Runs `pcbkit sch`, then builds kicad/<stem>.kicad_pcb: each footprint of the
    netlist at its position in layout.py, the outline, the mounting holes and the
    stackup. A part with no position is parked below the board and listed. ERC
    findings are listed too: the board is placed anyway, and the exit code is 1 if ERC
    reported errors. Needs pcbnew.
    """
    env.require_pcbnew()
    from pcbkit import place

    proj = load_project()
    result = sch.build_schematic(proj)
    click.echo(sch.format_erc(result.erc, result.erc_report, proj.root))
    placed = place.place_board(proj)
    click.echo(f"placed {placed.placed}, missing: {placed.missing}")
    if placed.missing:
        click.echo("  (parked below the board: give each a position in layout.py)")
    if result.erc.errors:
        click.echo("ERC reported errors: fix them in design.py before routing.")
        click.get_current_context().exit(1)


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

    Starts from the placed board that `pcbkit build` wrote. Runs the project's
    routing.py hooks, then Freerouting, and after each run fills the pours and runs DRC:
    it stops at the first try with no copper problem, and otherwise gives up after
    --tries tries and leaves the best board. Then it adds the silkscreen and shows the
    DRC with schematic parity. A Freerouting run that never starts routing is killed
    after [route] stall_timeout_s; with --eco that falls back to routing the whole
    board. Exits 1 if no try was clean.
    """
    env.require_pcbnew()
    proj = load_project()
    from pcbkit.route import flow

    result = flow.route(proj, eco=eco, tries=tries, passes=passes)
    if not result.clean:
        click.get_current_context().exit(1)


@cli.command("promote")
def promote_cmd() -> None:
    """Save the routed board as the golden route.

    Copies the route that `pcbkit route` made (prerouted.kicad_pcb, the session file
    and the DSN) into golden/, which `pcbkit finalize` rebuilds from. It first runs DRC
    with schematic parity on the finished board and refuses to promote one that has
    any violation, unconnected pad or footprint error.
    """
    env.require_pcbnew()
    proj = load_project()
    from pcbkit.route import flow

    flow.promote(proj)


@cli.command("finalize")
@click.option(
    "--no-render", is_flag=True, help="Skip the 3D renders, which take the longest."
)
def finalize_cmd(no_render: bool) -> None:
    """Rebuild from the golden route, run DRC and export the fab files.

    Copies golden/ into kicad/, imports the route, fills the pours, adds the
    silkscreen, runs DRC with schematic parity and exports the fab files and
    documents into out/. If DRC is not clean it stops before the export and exits 1.
    """
    env.require_pcbnew()
    proj = load_project()
    from pcbkit.route import flow

    flow.finalize(proj, render=not no_render)


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

    Reads the files `pcbkit finalize` exported (out/fab, else fab): the Gerber zip for
    the board (layers, size, thickness, copper, finish, smallest track, space and hole)
    and the BOM for the assembly (unique parts, SMD placements, BGA/QFP/QFN parts,
    through-hole parts and their designators). With --assembled the assembly numbers
    are printed too; --self-solder-tht leaves the through-hole parts out of them. With
    --notes the order notes must fit the form's 600 characters, or the command fails
    and says by how many it is over.
    """
    from pcbkit.fab import pcbway

    if self_solder_tht and assembled is None:
        raise click.UsageError(
            "--self-solder-tht only applies to assembly: add --assembled N"
        )
    if assembled is not None and fab_qty is not None and assembled > fab_qty:
        raise click.UsageError(
            f"--assembled {assembled} is more than --fab-qty {fab_qty}: you cannot "
            "assemble more boards than you have made"
        )
    notes_chars = pcbway.check_notes_file(notes) if notes is not None else None
    result = pcbway.build_quote(
        load_project(),
        assembled=assembled,
        fab_qty=fab_qty,
        self_solder_tht=self_solder_tht,
        notes_chars=notes_chars,
    )
    click.echo(pcbway.format_quote(result))


@cli.command("compare")
@click.argument("old", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("new", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@click.option(
    "--piece-tol",
    type=click.FloatRange(min=0),
    default=0.01,
    show_default=True,
    metavar="MM2",
    help="Copper on one board only is a difference when a piece is larger than this.",
)
@click.option(
    "--fill-tol",
    type=click.FloatRange(min=0),
    default=0.5,
    show_default=True,
    metavar="MM2",
    help="A zone's fill is a difference when it moved by this much or more.",
)
@click.option(
    "--top",
    type=click.IntRange(min=0),
    default=8,
    show_default=True,
    metavar="N",
    help="Pieces to list for each layer and board in the text report.",
)
def compare_cmd(
    old: Path, new: Path, as_json: bool, piece_tol: float, fill_tol: float, top: int
) -> None:
    """Compare the copper of two boards; exit 1 if they differ.

    Compares the number and length of tracks, the number of vias, each zone's filled
    area, and the copper itself on F.Cu and B.Cu: what exists on one board only,
    judged piece by piece. Exits 0 when the boards match, 1 when they differ and 2
    when a file cannot be read as a board.
    """
    env.require_pcbnew()
    import json

    from pcbkit import compare

    report = compare.compare_boards(
        old, new, piece_tol_mm2=piece_tol, fill_tol_mm2=fill_tol
    )
    if as_json:
        click.echo(json.dumps(compare.report_to_dict(report), indent=2))
    else:
        click.echo(compare.format_report(report, top=top))
    if report.differs:
        click.get_current_context().exit(1)


@cli.command("shots")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    metavar="DIR",
    help="Folder for the shots (default: out/shots in the project).",
)
@click.option(
    "--pcb",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    metavar="FILE",
    help="Board to shoot (default: the project's kicad/<stem>.kicad_pcb).",
)
@click.option(
    "--region",
    "names",
    multiple=True,
    metavar="NAME",
    help="Only this region; repeat the option for more (default: every region).",
)
@click.option(
    "--no-render", is_flag=True, help="Skip the 3D renders, which take the longest."
)
def shots_cmd(
    out_dir: Path | None, pcb: Path | None, names: tuple[str, ...], no_render: bool
) -> None:
    """Export crops and renders of named board regions for review.

    Saves each region as an SVG and a PNG, and three 3D renders. The regions are the
    whole board, top and bottom, and the ones the project names in SHOTS in layout.py.
    Needs rsvg-convert for the PNGs.
    """
    from pcbkit import shots

    proj = load_project()
    if pcb is None:
        pcb = proj.kicad_dir / f"{proj.config.board.stem}.kicad_pcb"
        if not pcb.is_file():
            raise click.ClickException(
                f"no board at {pcb}: run `pcbkit finalize`, or give a board with --pcb"
            )
    regions = shots.select_regions(shots.load_regions(proj), names)
    result = shots.take_shots(
        pcb, out_dir or proj.out_dir / "shots", regions, render=not no_render
    )
    click.echo(shots.format_result(result, proj.root))
