"""The pcbkit command line.

One function per command, in workflow order. Command functions are called
``<command>_cmd`` and registered under an explicit name, so a command named ``check`` or
``report`` never shadows the module of the same name that it imports.

Tier 2 commands (build, route, promote, finalize, check, mutants, compare) need
pcbnew. Each calls ``pcbkit.kicad.env.require_pcbnew()`` first, so a missing pcbnew is a
message rather than a traceback. ``shots`` is tier 1: it needs only kicad-cli and
rsvg-convert.
"""

from __future__ import annotations

import platform
from pathlib import Path

import click

from pcbkit import __version__, doctor, sch
from pcbkit.kicad import env
from pcbkit.project import load_project


class _WorkflowGroup(click.Group):
    """A command group that lists its commands in workflow order, not alphabetically."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


@click.group(
    cls=_WorkflowGroup, context_settings={"help_option_names": ["-h", "--help"]}
)
@click.version_option(package_name="pcbkit", prog_name="pcbkit")
def cli() -> None:
    """Design, route, check and export KiCad boards from Python.

    Run the commands inside a board project: a folder with a pcbkit.toml. They are
    listed in the order you use them. Commands that need KiCad's pcbnew module (build,
    route, promote, finalize, check, mutants, compare) run in the project's own .venv,
    which `pcbkit setup` creates; the rest run anywhere.
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


@cli.group("datasheet", cls=_WorkflowGroup)
def datasheet_group() -> None:
    """Find a part's datasheet, check it is the part's, and record it.

    `find` tries Mouser (with MOUSER_API_KEY) and KiCad's stock symbols and judges
    each PDF by its own text; `confirm` settles a candidate with a page and a quote.
    Each part's answer goes to parts/<part>.datasheet.json, in the board project when
    run inside one. `find` exits 3 when a part is not verified or confirmed. Runs
    anywhere; needs pdftotext.
    """


@datasheet_group.command("find")
@click.argument("mpns", nargs=-1, required=True, metavar="PART_NUMBER...")
@click.option("--url", help="Judge this datasheet link only (one part number).")
@click.option("--maker", help="The manufacturer, when Mouser does not know the part.")
@click.option(
    "--built",
    is_flag=True,
    help="The part number is built from a series code (resistors, capacitors...).",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Write this run's answer even over a better record.",
)
@click.option(
    "--into",
    type=click.Path(file_okay=False, path_type=Path),
    help="The folder for the records [default: the project's parts/].",
)
def datasheet_find_cmd(
    mpns: tuple[str, ...],
    url: str | None,
    maker: str | None,
    built: bool,
    replace: bool,
    into: Path | None,
) -> None:
    """Find each part's datasheet, judge it, and record the answer.

    A datasheet is VERIFIED when its text holds the part's whole orderable number and
    its maker's name. Anything less is a CANDIDATE, with a reason and the next step to
    take, or NOT FOUND. Mouser is asked ten part numbers at a time and its answers are
    cached, as are the PDFs, so running it again is cheap. A run that finds less than
    the record already holds (no key, offline) leaves the record as it is.
    """
    from pcbkit import datasheet_find as finding

    if url and len(mpns) > 1:
        raise click.UsageError("--url judges one part's datasheet: give one number")
    if maker and len(mpns) > 1:
        raise click.UsageError("--maker names one part's maker: give one number")
    if url and not finding.normal_link(url):
        raise click.UsageError(f"--url takes an http or https link, not {url!r}")
    folder = into or finding.default_folder()
    olds = {mpn: finding.read_record(folder, mpn) for mpn in mpns}
    maker_from = "--maker"
    if url and not maker and (olds[mpns[0]] or {}).get("manufacturer"):
        maker, maker_from = olds[mpns[0]]["manufacturer"], "the earlier record"
    index = None if url else finding.symbol_index(click.echo)
    notes: list[str] = []
    matches: dict[str, list[dict[str, object]]] = {}
    asked: set[str] = set()
    if not (url and maker):
        matches, asked = finding.mouser_parts(list(mpns), notes.append)
    unverified = 0
    for number, mpn in enumerate(mpns):
        old = olds[mpn]
        record = finding.find(
            mpn,
            url=finding.normal_link(url) if url else None,
            maker=maker,
            built=built,
            matches=matches.get(mpn, []),
            index=index,
            notes=notes,
            mouser_asked=mpn in asked,
            maker_from=maker_from,
        )
        dropped = finding.keep_confirmation(old, record)
        if dropped:
            record["notes"].append(dropped)
        path = finding.record_path(folder, mpn)
        if old is not None and not replace and not finding.supersedes(old, record):
            found = record["status"]
            if record["chosen"] is not None and record["status"] == "CANDIDATE":
                found += (
                    f" ({record['candidates'][record['chosen']]['verdict']['reason']})"
                )
            shown = dict(
                old,
                notes=[
                    *record["notes"],
                    f"kept: this run found only {found}; --replace writes it",
                ],
            )
            click.echo(("\n" if number else "") + finding.describe(shown, path))
            record = old
        else:
            finding.write_record(folder, record)
            click.echo(("\n" if number else "") + finding.describe(record, path))
        unverified += record["status"] not in ("VERIFIED", finding.CONFIRMED)
    if unverified:
        click.get_current_context().exit(finding.NOT_VERIFIED)


@datasheet_group.command("confirm")
@click.argument("mpn", metavar="PART_NUMBER")
@click.option(
    "--page",
    type=int,
    required=True,
    help="The page of the PDF, counted from 1 in the file's own order.",
)
@click.option("--quote", required=True, help="Text copied from that page.")
@click.option(
    "--into",
    type=click.Path(file_okay=False, path_type=Path),
    help="The folder for the records [default: the project's parts/].",
)
def datasheet_confirm_cmd(mpn: str, page: int, quote: str, into: Path | None) -> None:
    """Confirm a candidate datasheet with a page and a quote from it.

    Only a candidate the text check could not settle can be confirmed: a part number
    built from a series code (quote the series code where the datasheet explains it),
    or a maker the text does not name (quote the whole part number). The PDF is judged
    again and the quote must be on that page.
    """
    from pcbkit import datasheet_find as finding

    folder = into or finding.default_folder()
    record = finding.confirm(folder, mpn, page, quote)
    click.echo(finding.describe(record, finding.record_path(folder, mpn)))


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

    Runs `pcbkit sch`, then builds `kicad/<stem>.kicad_pcb`: each footprint of the
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
    from pcbkit.check import results as check_results
    from pcbkit.check import runner

    code = runner.run(proj, expression)
    results = check_results.results_dir(proj) / check_results.RESULTS_FILE
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

    Turns out/checks/results.json (left by `pcbkit check`) into
    out/checks/VALIDATION.md: the counts, then every check with its result and the
    numbers it recorded. A run that -k cut short is marked as partial. Exits 1 if the
    last run had a failed check or an error, or ran no checks. Runs anywhere: it only
    reads the results file.
    """
    proj = load_project()
    from pcbkit import report

    path, data = report.write_report(proj)
    click.echo(f"{report.summary_line(data)}\nReport: {path.relative_to(proj.root)}")
    note = report.partial_note(data)
    if note:
        click.echo(note)
    counts = data.get("counts", {})
    if not data["checks"]:
        click.echo(
            "The last run ran no checks: nothing matched? Run `pcbkit check` again."
        )
        click.get_current_context().exit(1)
    if counts.get("failed") or counts.get("error"):
        click.get_current_context().exit(1)


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
    help="Board to shoot (default: the project's `kicad/<stem>.kicad_pcb`).",
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
