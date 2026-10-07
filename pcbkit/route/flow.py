"""The routing commands: ``route``, ``promote`` and ``finalize``.

Each is a short sequence of stages from this package and from the other work packages,
so the functions here do little but call them in order and say what happened:

* ``route``: ``pre`` (or ``eco``), then Freerouting in the retry loop (``post`` and a
  DRC after each run), then the silkscreen and a DRC with schematic parity.
* ``promote``: keep the routed board's route in ``golden/``, if its DRC is clean.
* ``finalize``: rebuild the board from ``golden/`` (``post``), add the silkscreen, run
  DRC with schematic parity, and export the fab files.

The silkscreen (``pcbkit.silk``) and the fab export (``pcbkit.fab``) belong to other
work packages and are imported where they are used.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from pcbkit.kicad import cli as kicad_cli
from pcbkit.project import Project
from pcbkit.route import eco as eco_stage
from pcbkit.route import freerouting
from pcbkit.route import post as post_stage
from pcbkit.route import pre as pre_stage
from pcbkit.route.eco import EcoResult
from pcbkit.route.files import golden_files, route_files


def format_drc(report: kicad_cli.DrcReport) -> str:
    """Return a DRC report as text: the three totals, then each category's count."""
    lines = [
        f"DRC: {report.drc_violations} violations, {report.unconnected} unconnected "
        f"pads, {report.footprint_errors} footprint errors"
    ]
    lines += [f"  {n:4d} {name}" for name, n in report.categories.items()]
    return "\n".join(lines)


@dataclass(frozen=True)
class RouteResult:
    """What ``route`` did.

    ``loop`` is how the Freerouting tries went, ``eco`` what the eco stage kept (None
    for a full route) and ``drc`` the final DRC with schematic parity, which is None if
    no try made a board. ``clean`` is True if a try's copper was clean.
    """

    loop: freerouting.LoopResult
    eco: EcoResult | None
    drc: kicad_cli.DrcReport | None
    pcb: Path

    @property
    def clean(self) -> bool:
        """Return True if a try ended with no copper problem."""
        return self.loop.clean


def route(
    proj: Project,
    eco: Path | None = None,
    tries: int | None = None,
    passes: int | None = None,
    say: Callable[[str], None] = click.echo,
) -> RouteResult:
    """Route the placed board until its copper is clean, or the tries run out.

    ``eco`` is a ``golden/``-style folder whose route is kept; ``tries`` and ``passes``
    default to ``[route]`` of pcbkit.toml. Whatever the outcome, the best board is left
    in ``kicad/`` with the silkscreen on it, and the DRC with schematic parity is shown.
    """
    files = route_files(proj)
    config = proj.config.route
    tries = config.tries if tries is None else tries
    passes = config.freerouting_passes if passes is None else passes
    if not files.placed.is_file():
        raise click.ClickException(
            f"{files.placed} not found: run `pcbkit build` first"
        )
    router = freerouting.find_router()

    eco_result = None
    if eco is not None:
        eco_result = eco_stage.eco(proj, Path(eco))
        say(eco_result.summary())
    else:
        pre_stage.pre(proj)

    def run() -> freerouting.RouterRun:
        return freerouting.run_router(files, router, passes, config.stall_timeout_s)

    def post() -> str:
        return post_stage.post(proj).summary()

    def drc() -> Mapping[str, int]:
        return kicad_cli.drc(files.pcb, files.drc).report.categories

    def fall_back() -> None:
        pre_stage.pre(proj)

    # A try's board and session are set aside when it is the best so far, so that
    # the next try cannot overwrite them; the best is put back if no try is clean.
    kept = (files.pcb, files.ses, files.drc)
    with tempfile.TemporaryDirectory(prefix=".best-", dir=files.kicad) as spare:
        spare_dir = Path(spare)

        def keep(attempt: freerouting.Attempt) -> None:
            for path in kept:
                shutil.copyfile(path, spare_dir / path.name)

        def restore(attempt: freerouting.Attempt) -> None:
            for path in kept:
                shutil.copyfile(spare_dir / path.name, path)

        loop = freerouting.route_loop(
            tries,
            run,
            post,
            drc,
            fall_back=fall_back if eco is not None else None,
            keep=keep,
            restore=restore,
            say=say,
        )

    final = None
    if loop.best is not None:
        if not loop.clean:
            say(f"the best board is {files.pcb}")
        from pcbkit.silk import apply_silk, format_result

        say(format_result(apply_silk(proj), proj.root))
        final = kicad_cli.drc(files.pcb, files.drc, schematic_parity=True).report
        say(format_drc(final))
    return RouteResult(loop, eco_result, final, files.pcb)


def promote(proj: Project, say: Callable[[str], None] = click.echo) -> list[Path]:
    """Copy the routed board's route into ``golden/``; return the files written.

    The route is ``prerouted.kicad_pcb``, the session file and the DSN. ``golden/``
    holds no project file: ``post`` applies the rules from code and writes it again.
    ``golden/`` is the route that passed DRC, so this runs DRC with schematic parity
    on the finished board first and refuses unless it is 0/0/0.
    """
    files = route_files(proj)
    golden = golden_files(proj)
    needed = {"prerouted": files.prerouted, "ses": files.ses, "dsn": files.dsn}
    for path in (*needed.values(), files.pcb):
        if not path.is_file():
            raise click.ClickException(f"{path} not found: run `pcbkit route` first")
    report = kicad_cli.drc(files.pcb, files.drc, schematic_parity=True).report
    if not report.clean:
        raise click.ClickException(
            f"not promoting: {files.pcb.name} fails DRC\n{format_drc(report)}\n"
            "golden/ holds only a route that passed with 0 violations, 0 unconnected "
            "pads and 0 footprint errors"
        )
    proj.golden_dir.mkdir(exist_ok=True)
    written = []
    for role, source in needed.items():
        shutil.copyfile(source, golden[role])
        written.append(golden[role])
    say("promoted to golden/: " + ", ".join(path.name for path in written))
    return written


def copy_golden(proj: Project) -> list[Path]:
    """Copy every file of ``golden/`` into ``kicad/``; return the files written.

    ``prerouted.kicad_pcb`` and the session file must be there. Hidden files and
    folders are left alone.
    """
    files = route_files(proj)
    golden = golden_files(proj)
    for role in ("prerouted", "ses"):
        if not golden[role].is_file():
            raise click.ClickException(
                f"{golden[role]} not found: `pcbkit promote` writes golden/ "
                "after a route that passed DRC"
            )
    files.kicad.mkdir(exist_ok=True)
    written = []
    for source in sorted(proj.golden_dir.iterdir()):
        if source.is_file() and not source.name.startswith("."):
            target = files.kicad / source.name
            shutil.copyfile(source, target)
            written.append(target)
    return written


def finalize(
    proj: Project, render: bool = True, say: Callable[[str], None] = click.echo
) -> kicad_cli.DrcReport:
    """Rebuild the board from ``golden/``, check it and export the fab files.

    In order: copy ``golden/`` into ``kicad/``, ``post``, the silkscreen, DRC with
    schematic parity, then the fab export (``render`` says whether it makes the 3D
    renders). If the DRC is not clean nothing is exported: the files would be for a
    board that fails its own checks. Return the DRC report.
    """
    files = route_files(proj)
    if not files.schematic.is_file():
        raise click.ClickException(
            f"{files.schematic} not found: run `pcbkit build` first, DRC compares "
            "the board with the schematic"
        )
    copy_golden(proj)
    say(post_stage.post(proj).summary())
    from pcbkit.silk import apply_silk, format_result

    say(format_result(apply_silk(proj), proj.root))
    report = kicad_cli.drc(files.pcb, files.drc, schematic_parity=True).report
    say(format_drc(report))
    if not report.clean:
        raise click.ClickException(
            "DRC is not clean: nothing was exported. Fix the board, or promote a "
            "route that passes."
        )
    from pcbkit.fab import export_fab

    made = export_fab(proj, render=render)
    say(f"BOM lines: {made.bom_lines} total parts: {made.total_parts}")
    for problem in made.without_mpn:
        say(f"  warning: no orderable part number: {problem}")
    return report
