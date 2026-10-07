"""Run kicad-cli, and read the ERC and DRC reports it writes.

Two halves, kept apart so the second needs no KiCad at all:

* Wrappers: ``erc``, ``export_netlist``, ``drc``, ``export_gerbers``, ``export_drill``,
  ``export_positions``, ``export_svg``, ``render_3d``, ``export_sch_pdf`` and
  ``export_pcb_pdf``. Each builds the command line the board scripts have always used,
  runs it through ``env._run`` (so unit tests can fake the machine) and returns a small
  frozen result. A wrapper raises ``KicadCliError`` when kicad-cli cannot be found,
  exits non-zero, or does not write what it was asked to. A design that has violations
  is not a failure: kicad-cli exits 0 for those, and the wrappers do not add
  ``--exit-code-violations``.
* Parsers: ``parse_erc`` and ``parse_drc`` turn report text into ``ErcReport`` and
  ``DrcReport``. They are pure functions and use the report as its own oracle: the
  entries found under each "Found N" heading must number N, so a report in a format
  they do not know raises ``ReportError`` instead of reading as clean.

Positions in a DRC or ERC report are KiCad page coordinates in millimetres, so they
include the (50, 50) offset that ``pcbkit.kicad.board`` adds to layout coordinates.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import click

from pcbkit.kicad import env, quiet

StrPath = Union[str, "os.PathLike[str]"]

# The layers PCBWay gets, in the order the fab export has always listed them.
GERBER_LAYERS = (
    "F.Cu",
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
)

# Seconds to wait for each kind of run. Renders keep the 300 s finalize.sh gave them;
# a DRC that refills zones on a big board is the slowest of the rest.
TIMEOUT_ERC = 300.0
TIMEOUT_NETLIST = 300.0
TIMEOUT_DRC = 900.0
TIMEOUT_EXPORT = 300.0
TIMEOUT_RENDER = 300.0

# The three headings of a DRC report, spelled as KiCad prints them.
DRC_VIOLATIONS = "DRC violations"
DRC_UNCONNECTED = "unconnected pads"
DRC_FOOTPRINT = "Footprint errors"

_MM_PER_UNIT = {"mm": 1.0, "mils": 0.0254, "in": 25.4}


class ReportError(ValueError):
    """A report this parser cannot read, or one that contradicts itself."""


class KicadCliError(click.ClickException):
    """A kicad-cli run that failed: it exited non-zero, timed out or wrote nothing."""

    def __init__(self, message: str, command: Sequence[str], output: str = "") -> None:
        """Keep the command line and the tool's own output beside the message."""
        super().__init__(message)
        self.command = tuple(command)
        self.output = output


@dataclass(frozen=True)
class CliRun:
    """A kicad-cli run that succeeded.

    ``args`` is the whole command line, kicad-cli first. ``files`` lists what the run
    wrote: the file itself for an export to a file, and for an export into a folder
    everything in that folder afterwards, sorted.
    """

    args: tuple[str, ...]
    output: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class Violation:
    """One entry of an ERC or DRC report: what is wrong, how badly, and where.

    ``section`` is the heading the entry sits under: for DRC the printed name of the
    section ("DRC violations", "unconnected pads" or "Footprint errors"), for ERC the
    sheet path. ``rule`` is the rule's name as printed ("Local override" when the
    severity was set on the project, empty in an ERC report) and ``severity`` is
    "error" or "warning" (empty if the line was not understood). ``positions`` are in
    millimetres, one per object the entry points at, and ``items`` describes those
    objects in the same order. ``nets`` are the net names found in the descriptions of
    a DRC entry, as printed (with their leading "/" when KiCad prints one), in order
    and without repeats; an ERC entry has none.
    """

    category: str
    message: str
    severity: str
    rule: str
    section: str
    positions: tuple[tuple[float, float], ...]
    nets: tuple[str, ...]
    items: tuple[str, ...]


@dataclass(frozen=True)
class ErcReport:
    """A parsed ERC report.

    ``summary`` is the report's own totals text, such as
    "ERC messages: 0  Errors 0  Warnings 0", exactly as printed.
    """

    messages: int
    errors: int
    warnings: int
    summary: str
    violations: tuple[Violation, ...]

    @property
    def categories(self) -> dict[str, int]:
        """Return how many entries each category has, in order of first appearance."""
        return count_categories(self.violations)

    @property
    def clean(self) -> bool:
        """Return True when the report holds no messages at all."""
        return self.messages == 0


@dataclass(frozen=True)
class DrcReport:
    """A parsed DRC report.

    ``counts`` maps each heading's printed name to its number, in print order:
    {"DRC violations": 0, "unconnected pads": 0, "Footprint errors": 0}.
    """

    board: str
    counts: dict[str, int]
    violations: tuple[Violation, ...]

    @property
    def drc_violations(self) -> int:
        """Return the number printed for "DRC violations"."""
        return self.counts[DRC_VIOLATIONS]

    @property
    def unconnected(self) -> int:
        """Return the number printed for "unconnected pads"."""
        return self.counts[DRC_UNCONNECTED]

    @property
    def footprint_errors(self) -> int:
        """Return the number printed for "Footprint errors"."""
        return self.counts[DRC_FOOTPRINT]

    @property
    def categories(self) -> dict[str, int]:
        """Return how many entries each category has, in order of first appearance."""
        return count_categories(self.violations)

    @property
    def clean(self) -> bool:
        """Return True when every count is zero."""
        return not any(self.counts.values())


@dataclass(frozen=True)
class ErcResult:
    """An ERC run: the command, where the report went, and what it says."""

    run: CliRun
    report_path: str
    report: ErcReport


@dataclass(frozen=True)
class DrcResult:
    """A DRC run: the command, where the report went, and what it says."""

    run: CliRun
    report_path: str
    report: DrcReport


# --- running kicad-cli -----------------------------------------------------------


def kicad_cli_path() -> str:
    """Return the path of kicad-cli, or raise a ClickException saying how to get it."""
    tool = env.find_kicad_cli()
    if tool is None:
        raise click.ClickException(
            "kicad-cli not found: install KiCad 10 (brew install --cask kicad), "
            "or put its folder on PATH. `pcbkit doctor` shows what is missing."
        )
    return tool.path


def _tail(text: str, lines: int = 6) -> str:
    """Return the last few non-empty lines of a tool's output, without wx noise.

    A wx assertion or debug line (see ``pcbkit.kicad.quiet``) is dropped before the
    count, so it cannot push a real error line out of the tail.
    """
    kept = [line for line in quiet.strip_noise(text).splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def _subcommand(args: Sequence[str]) -> str:
    """Return the words before the first option: "pcb export gerbers"."""
    words: list[str] = []
    for word in args:
        if word.startswith("-"):
            break
        words.append(word)
    return " ".join(words)


def _listing(path: StrPath) -> tuple[str, ...]:
    """Return a file as itself, or a folder's files sorted; nothing if it is absent."""
    found = Path(path)
    if found.is_dir():
        return tuple(sorted(str(item) for item in found.iterdir() if item.is_file()))
    return (str(found),) if found.is_file() else ()


def run_cli(
    args: Sequence[str], *, timeout: float, expect: Sequence[StrPath] = ()
) -> CliRun:
    """Run ``kicad-cli`` with ``args`` and return the run, or raise KicadCliError.

    ``expect`` names what the command must leave behind: a file it writes, or a folder
    it exports into. A file left by an earlier run is deleted first, so one that
    exists afterwards was written by this run: a quiet failure cannot pass as an
    export, and a stale report cannot read as a clean one. A run that exits 0 but
    leaves a file missing, or a folder empty, raises too. A folder is not cleared
    (it may hold other work): files already in it stay, and are listed with the new.
    """
    command = (kicad_cli_path(), *map(str, args))
    for path in expect:
        if Path(path).is_file():
            Path(path).unlink()
    done = env._run(list(command), timeout=timeout)
    name = f"kicad-cli {_subcommand(command[1:])}"
    if done.returncode != 0:  # env._run reports a timeout or a launch failure as -1
        reason = done.error or f"exit {done.returncode}"
        raise KicadCliError(
            f"{name} failed ({reason})\n{_tail(done.output)}".rstrip(),
            command,
            done.output,
        )
    files: list[str] = []
    for path in expect:
        found = _listing(path)
        if not found:
            gist = f"{name} ran but wrote nothing at {path}"
            raise KicadCliError(
                f"{gist}\n{_tail(done.output)}".rstrip(), command, done.output
            )
        files.extend(found)
    return CliRun(command, done.output, tuple(files))


# --- the wrappers ----------------------------------------------------------------


def erc(sch: StrPath, report: StrPath, *, severity_all: bool = False) -> ErcResult:
    """Run ERC on a schematic, write the report, and return it parsed.

    ``severity_all`` adds ``--severity-all`` (the validation check uses it; the
    schematic build does not).
    """
    args = ["sch", "erc"]
    if severity_all:
        args.append("--severity-all")
    args += ["-o", str(report), str(sch)]
    run = run_cli(args, timeout=TIMEOUT_ERC, expect=[report])
    return ErcResult(run, str(report), parse_erc(_read(report)))


def export_netlist(sch: StrPath, out: StrPath) -> CliRun:
    """Export the schematic's netlist in KiCad's s-expression format."""
    args = ["sch", "export", "netlist", "--format", "kicadsexpr"]
    args += ["-o", str(out), str(sch)]
    return run_cli(args, timeout=TIMEOUT_NETLIST, expect=[out])


def drc(
    pcb: StrPath,
    report: StrPath,
    *,
    schematic_parity: bool = False,
    severity_all: bool = False,
) -> DrcResult:
    """Run DRC on a board, write the report, and return it parsed.

    ``schematic_parity`` adds ``--schematic-parity``, which compares the board with the
    schematic next to it; ``severity_all`` adds ``--severity-all``.
    """
    args = ["pcb", "drc"]
    if severity_all:
        args.append("--severity-all")
    if schematic_parity:
        args.append("--schematic-parity")
    args += ["-o", str(report), str(pcb)]
    run = run_cli(args, timeout=TIMEOUT_DRC, expect=[report])
    return DrcResult(run, str(report), parse_drc(_read(report)))


def export_gerbers(
    pcb: StrPath,
    out_dir: StrPath,
    *,
    layers: Sequence[str] = GERBER_LAYERS,
    protel_ext: bool = False,
    use_drill_origin: bool = True,
) -> CliRun:
    """Plot the Gerber files into ``out_dir``; kicad-cli writes the job file there too.

    The job file is the ``*-job.gbrjob`` among the returned ``files``. The defaults are
    the fab export's: KiCad's own file extensions, and the drill/place origin.
    """
    args = ["pcb", "export", "gerbers", "--layers", ",".join(layers)]
    if not protel_ext:
        args.append("--no-protel-ext")
    if use_drill_origin:
        args.append("--use-drill-file-origin")
    args += ["-o", os.path.join(str(out_dir), ""), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out_dir])


def export_drill(
    pcb: StrPath,
    out_dir: StrPath,
    *,
    origin: str = "plot",
    units: str = "mm",
    separate_th: bool = True,
    generate_map: bool = True,
    map_format: str = "gerberx2",
) -> CliRun:
    """Write the Excellon drill files (and their maps) into ``out_dir``.

    Run it after ``export_gerbers`` with the same folder and the same origin, so the
    holes line up with the copper.
    """
    args = ["pcb", "export", "drill", "--format", "excellon"]
    if separate_th:
        args.append("--excellon-separate-th")
    args += ["--drill-origin", origin]
    if generate_map:
        args += ["--generate-map", "--map-format", map_format]
    args += ["--excellon-units", units]
    args += ["-o", os.path.join(str(out_dir), ""), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out_dir])


def export_positions(
    pcb: StrPath,
    out: StrPath,
    *,
    side: str = "front",
    fmt: str = "csv",
    units: str = "mm",
    use_drill_origin: bool = True,
) -> CliRun:
    """Write the component position (pick-and-place) file."""
    args = ["pcb", "export", "pos", "--side", side, "--format", fmt, "--units", units]
    if use_drill_origin:
        args.append("--use-drill-file-origin")
    args += ["-o", str(out), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out])


def export_svg(
    pcb: StrPath,
    out: StrPath,
    layers: Sequence[str],
    *,
    mode_single: bool = True,
    exclude_drawing_sheet: bool = True,
    fit_page_to_board: bool = True,
    black_and_white: bool = False,
) -> CliRun:
    """Export ``layers`` of the board as one SVG, fitted to the board by default."""
    args = ["pcb", "export", "svg", "--layers", ",".join(layers)]
    if mode_single:
        args.append("--mode-single")
    if exclude_drawing_sheet:
        args.append("--exclude-drawing-sheet")
    if fit_page_to_board:
        args.append("--fit-page-to-board")
    if black_and_white:
        args.append("--black-and-white")
    args += ["-o", str(out), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out])


def render_3d(
    pcb: StrPath,
    out: StrPath,
    *,
    side: str = "top",
    rotate: Sequence[float] | None = None,
    width: int | None = None,
    height: int | None = None,
    quality: str | None = None,
    zoom: float | None = None,
) -> CliRun:
    """Render the board in 3D to a PNG. Leave an option out to keep kicad-cli's default.

    ``rotate`` is (x, y, z) degrees, for example (-40, 0, -20) for an isometric view.
    """
    args = ["pcb", "render", "--side", side]
    if rotate is not None:
        args += ["--rotate", ",".join(f"{angle:g}" for angle in rotate)]
    if width is not None:
        args += ["--width", str(width)]
    if height is not None:
        args += ["--height", str(height)]
    if quality is not None:
        args += ["--quality", quality]
    if zoom is not None:
        args += ["--zoom", f"{zoom:g}"]
    args += ["-o", str(out), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_RENDER, expect=[out])


def export_sch_pdf(sch: StrPath, out: StrPath) -> CliRun:
    """Export the schematic as a PDF, one page per sheet."""
    args = ["sch", "export", "pdf", "-o", str(out), str(sch)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out])


def export_pcb_pdf(
    pcb: StrPath,
    out: StrPath,
    layers: Sequence[str],
    *,
    mode_single: bool = True,
) -> CliRun:
    """Export ``layers`` of the board as a PDF, all on one page when ``mode_single``."""
    args = ["pcb", "export", "pdf", "--layers", ",".join(layers)]
    if mode_single:
        args.append("--mode-single")
    args += ["-o", str(out), str(pcb)]
    return run_cli(args, timeout=TIMEOUT_EXPORT, expect=[out])


def _read(path: StrPath) -> str:
    """Return the text of a report file."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


# --- the report parsers ----------------------------------------------------------

_DRC_TITLE = re.compile(r"^\*\* Drc report for (?P<board>.+?) \*\*\s*$")
_DRC_FOUND = re.compile(r"^\*\* Found (?P<count>\d+) (?P<name>.+?) \*\*\s*$")
_ERC_SUMMARY = re.compile(
    r"ERC messages: (?P<messages>\d+)\s+Errors (?P<errors>\d+)"
    r"\s+Warnings (?P<warnings>\d+)"
)
_ERC_SHEET = re.compile(r"^\*{5} Sheet (?P<name>.*?)\s*$")
_ENTRY = re.compile(r"^\[(?P<category>[^\]\s]+)\]: (?P<message>.*?)\s*$")
_SEVERITY = re.compile(r"^\s+(?:Rule: )?(?P<rule>.*?); (?P<severity>\w+)\s*$")
_POSITION = re.compile(
    r"^\s+@\((?P<x>-?\d+(?:\.\d+)?) (?P<xu>mm|mils|in), "
    r"(?P<y>-?\d+(?:\.\d+)?) (?P<yu>mm|mils|in)\): (?P<what>.*?)\s*$"
)
# The net sits in the first [...] of a description, before any quoted text: "Track
# [/NET_A] on F.Cu", "Pad 1 [/NET_A] of R1". KiCad prints [<no net>] for none.
_NET = re.compile(r"^[^'\[]*?\[(?P<net>[^\]]*)\]")


def count_categories(violations: Sequence[Violation]) -> dict[str, int]:
    """Return how many violations each category has, in order of first appearance."""
    found: dict[str, int] = {}
    for item in violations:
        found[item.category] = found.get(item.category, 0) + 1
    return found


def _net_of(description: str) -> str:
    """Return the net named in an item's description, or "" if it names none."""
    found = _NET.match(description)
    net = found.group("net") if found else ""
    return "" if net == "<no net>" else net


def _blocks(
    text: str, heading: re.Pattern[str]
) -> list[tuple[str, str, str, list[str]]]:
    """Split a report into (section, category, message, indented lines) entries.

    A block starts at a "[category]: message" line and takes the indented lines under
    it; a blank line, a heading, an unindented line or the next entry ends it. The
    section is the ``name`` group of the latest ``heading`` line.
    """
    blocks: list[tuple[str, str, str, list[str]]] = []
    section: str | None = None
    body: list[str] | None = None
    for line in text.splitlines():
        head = heading.match(line)
        if head:
            section, body = head.group("name"), None
            continue
        entry = _ENTRY.match(line)
        if entry:
            if section is None:
                raise ReportError(f"report entry before any heading: {line.strip()}")
            body = []
            blocks.append(
                (section, entry.group("category"), entry.group("message"), body)
            )
        elif body is not None:
            if line.strip() and line[0].isspace():
                body.append(line)
            else:
                body = None
    return blocks


def _violation(
    section: str, category: str, message: str, body: Sequence[str], with_nets: bool
) -> Violation:
    """Build a Violation from one block: its rule and severity, then its objects."""
    rule = severity = ""
    positions: list[tuple[float, float]] = []
    items: list[str] = []
    for line in body:
        place = _POSITION.match(line)
        if place:
            x = float(place.group("x")) * _MM_PER_UNIT[place.group("xu")]
            y = float(place.group("y")) * _MM_PER_UNIT[place.group("yu")]
            positions.append((x, y))
            items.append(place.group("what"))
            continue
        said = _SEVERITY.match(line)
        if said and not severity:
            rule, severity = said.group("rule"), said.group("severity")
    nets: list[str] = []
    for what in items if with_nets else ():
        net = _net_of(what)
        if net and net not in nets:
            nets.append(net)
    return Violation(
        category,
        message,
        severity,
        rule,
        section,
        tuple(positions),
        tuple(nets),
        tuple(items),
    )


def parse_drc(text: str) -> DrcReport:
    """Parse the text of a ``kicad-cli pcb drc`` report.

    Raise ReportError if one of the three "Found N" headings is missing, or if a
    heading's number differs from the entries listed under it.
    """
    board = ""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        title = _DRC_TITLE.match(line)
        if title:
            board = title.group("board")
        found = _DRC_FOUND.match(line)
        if found:
            counts[found.group("name")] = int(found.group("count"))
    for name in (DRC_VIOLATIONS, DRC_UNCONNECTED, DRC_FOOTPRINT):
        if name not in counts:
            raise ReportError(
                f"no '** Found N {name} **' line in the DRC report: it is empty, "
                "truncated or in a format pcbkit does not know"
            )
    violations = tuple(
        _violation(*block, with_nets=True) for block in _blocks(text, _DRC_FOUND)
    )
    for name, count in counts.items():
        listed = sum(1 for item in violations if item.section == name)
        if listed != count:
            raise ReportError(
                f"the DRC report says {count} {name} but lists {listed} of them"
            )
    return DrcReport(board, counts, violations)


def parse_erc(text: str) -> ErcReport:
    """Parse the text of a ``kicad-cli sch erc`` report.

    Raise ReportError if the totals line is missing, or if the errors and warnings
    listed do not match the totals.
    """
    totals = _ERC_SUMMARY.search(text)
    if totals is None:
        raise ReportError(
            "no 'ERC messages: N  Errors N  Warnings N' line in the ERC report: it is "
            "empty, truncated or in a format pcbkit does not know"
        )
    violations = tuple(
        _violation(*block, with_nets=False) for block in _blocks(text, _ERC_SHEET)
    )
    errors = int(totals.group("errors"))
    warnings = int(totals.group("warnings"))
    for severity, said in (("error", errors), ("warning", warnings)):
        listed = sum(1 for item in violations if item.severity == severity)
        if listed != said:
            raise ReportError(
                f"the ERC report says {said} {severity}s but lists {listed} of them"
            )
    return ErcReport(
        messages=int(totals.group("messages")),
        errors=errors,
        warnings=warnings,
        summary=totals.group(0),
        violations=violations,
    )
