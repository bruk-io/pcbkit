"""PCBWay: the file names it is sent, and the values its order form asks for.

The names come from the board's ``fab_name``. The form values are read back from the
files that ``pcbkit finalize`` exported, not from the design, so they describe what is
about to be uploaded: the BOM CSV for the assembly numbers, and the Gerber zip for the
board (its job file gives the size, layers, thickness, finish, copper and design rules;
its drill files give the smallest hole). Nothing here needs KiCad or pcbnew.

PCBWay's definitions, as pcbkit reads them:

* Unique parts: the BOM lines PCBWay assembles. With the through-hole parts left to the
  customer that is the surface-mount lines only.
* SMD placements: the sum of the surface-mount lines' quantities, per board.
* BGA, QFP and QFN parts: counted by package family, from the footprint name (BGA, QFP
  or QFN anywhere in it, so LQFP, VQFN and LFBGA count). Pitch is not looked at.
* Through-hole parts: the sum of the through-hole lines' quantities, so a part that is
  two sockets under one reference counts twice, beside the number of references.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import click

from pcbkit.fab import bom
from pcbkit.fab.bom import BomLine
from pcbkit.project import Project

# The longest note the order form takes, in characters.
NOTES_LIMIT = 600

# Outer copper is 1 oz at this thickness; 2 oz is twice it.
OZ_MM = 0.035

# What KiCad calls a finish in the job file, and what the form calls it.
FINISH_NAMES = {"HAL lead-free": "HASL lead-free", "HAL SnPb": "HASL with lead"}

FINE_PITCH = re.compile(r"BGA|QFP|QFN", re.IGNORECASE)

# Spacing entries of the job file's design rules; MinLineWidth is the track width.
_SPACING_RULES = (
    "PadToPad",
    "PadToTrack",
    "TrackToTrack",
    "TrackToRegion",
    "RegionToRegion",
)

MIL_MM = 0.0254


class QuoteError(click.ClickException):
    """Report a file the quote needs but cannot read, or notes the form would refuse."""


@dataclass(frozen=True)
class FabNames:
    """The names of the files PCBWay is sent, all starting with the board's fab name."""

    bom_csv: str
    bom_xlsx: str
    centroid: str
    gerber_zip: str


def fab_names(fab_name: str) -> FabNames:
    """Return the file names for a board whose fab name is ``fab_name``."""
    return FabNames(
        bom_csv=f"{fab_name}_BOM.csv",
        bom_xlsx=f"{fab_name}_BOM.xlsx",
        centroid=f"{fab_name}_centroid.csv",
        gerber_zip=f"{fab_name}_gerbers.zip",
    )


# --- the notes ---------------------------------------------------------------------


def notes_length(text: str) -> int:
    """Return the characters of ``text`` the form would count.

    A line break counts as one character whether the file has CRLF or LF, and trailing
    whitespace does not count: an editor's final newline is not part of what you paste.
    """
    return len(text.replace("\r\n", "\n").replace("\r", "\n").rstrip())


def check_notes(text: str, source: str = "the notes") -> int:
    """Return the notes' length; raise QuoteError when it is over ``NOTES_LIMIT``."""
    length = notes_length(text)
    if length > NOTES_LIMIT:
        over = length - NOTES_LIMIT
        raise QuoteError(
            f"{source} are {length} characters; PCBWay's order notes take "
            f"{NOTES_LIMIT} ({over} too many)"
        )
    return length


def check_notes_file(path: Path) -> int:
    """Return the length of the notes in the file at ``path``; raise QuoteError if the
    file cannot be read as UTF-8 text or its notes are over ``NOTES_LIMIT``.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise QuoteError(f"the notes file {path} is not UTF-8 text") from None
    except OSError as err:
        raise QuoteError(f"cannot read the notes file {path}: {err.strerror}") from None
    return check_notes(text, f"the notes in {path}")


# --- designators -------------------------------------------------------------------


def compress_designators(refs: Iterable[str]) -> str:
    """Return ``refs`` as one line: "A1, C11, C12, J1, J10-J34, J38, J39".

    References are sorted by their letters, then their number. A run of three or more
    consecutive numbers with the same letters is written as a range; one or two stay
    separate.
    """
    runs: list[list[str]] = []
    for ref in sorted(refs, key=bom.refkey):
        letters, number = bom.refkey(ref)
        if runs:
            last_letters, last_number = bom.refkey(runs[-1][-1])
            if letters == last_letters and number == last_number + 1:
                runs[-1].append(ref)
                continue
        runs.append([ref])
    written: list[str] = []
    for run in runs:
        written += [f"{run[0]}-{run[-1]}"] if len(run) >= 3 else run
    return ", ".join(written)


# --- reading the exported files ----------------------------------------------------


@dataclass(frozen=True)
class BoardSpecs:
    """The bare-board values of the order form, as the Gerber files state them.

    A value the files do not carry is None (or "" for the finish).
    """

    layers: int
    width_mm: float
    height_mm: float
    thickness_mm: float
    copper_mm: tuple[float, ...]
    finish: str
    min_track_mm: float | None
    min_space_mm: float | None
    min_hole_mm: float | None


def _text(archive: zipfile.ZipFile, name: str) -> str:
    """Return a member of the zip as text."""
    return archive.read(name).decode("utf-8", errors="replace")


def read_board_specs(zip_path: Path) -> BoardSpecs:
    """Read the board's size, stackup, finish, rules and holes from the Gerber zip.

    The size is the job file's minus one outline line width: KiCad measures the outline
    with its stroke, which makes a 118 mm board 118.1 mm.
    """
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as err:
        raise QuoteError(f"cannot read {zip_path}: {err}") from None
    with archive:
        names = archive.namelist()
        jobs = [name for name in names if name.endswith(".gbrjob")]
        if not jobs:
            raise QuoteError(f"{zip_path.name} has no .gbrjob job file")
        try:
            job = json.loads(_text(archive, jobs[0]))
            general = job["GeneralSpecs"]
            width, height = general["Size"]["X"], general["Size"]["Y"]
            layers, thickness = general["LayerNumber"], general["BoardThickness"]
        except (ValueError, KeyError, TypeError) as err:
            raise QuoteError(
                f"the job file in {zip_path.name} is not one pcbkit can read: {err!r}"
            ) from None
        stroke = _outline_stroke(
            [_text(archive, name) for name in names if name.endswith("Edge_Cuts.gbr")]
        )
        holes = _drill_diameters(
            [_text(archive, name) for name in names if name.endswith(".drl")]
        )
    stackup = job.get("MaterialStackup", [])
    copper = tuple(
        float(layer["Thickness"])
        for layer in stackup
        if layer.get("Type") == "Copper" and "Thickness" in layer
    )
    rules = job.get("DesignRules", [])
    tracks = [r["MinLineWidth"] for r in rules if "MinLineWidth" in r]
    spaces = [r[key] for r in rules for key in _SPACING_RULES if key in r]
    return BoardSpecs(
        layers=int(layers),
        width_mm=round(float(width) - stroke, 3),
        height_mm=round(float(height) - stroke, 3),
        thickness_mm=float(thickness),
        copper_mm=copper,
        finish=str(general.get("Finish", "")),
        min_track_mm=float(min(tracks)) if tracks else None,
        min_space_mm=float(min(spaces)) if spaces else None,
        min_hole_mm=min(holes) if holes else None,
    )


def _outline_stroke(gerbers: Sequence[str]) -> float:
    """Return the widest round aperture in the outline Gerber, or 0 if none."""
    widths = [
        float(width)
        for text in gerbers
        for width in re.findall(r"^%ADD\d+C,([0-9.]+)\*%", text, re.MULTILINE)
    ]
    return max(widths, default=0.0)


def _drill_diameters(files: Sequence[str]) -> list[float]:
    """Return every tool diameter in the Excellon files' tool tables, in millimetres."""
    return [
        float(size)
        for text in files
        for size in re.findall(r"^T\d+C([0-9.]+)", text, re.MULTILINE)
    ]


def find_fab_dir(project: Project) -> Path:
    """Return the folder holding the fab files: ``out/fab``, else ``fab``.

    Raise QuoteError, saying what to run, when neither has the Gerber zip and the BOM.
    """
    names = fab_names(project.config.board.fab_name)
    for folder in (project.out_dir / "fab", project.fab_dir):
        if (folder / names.gerber_zip).is_file() and (folder / names.bom_csv).is_file():
            return folder
    raise QuoteError(
        f"no fab files: expected {names.gerber_zip} and {names.bom_csv} in "
        f"{project.out_dir / 'fab'} (or {project.fab_dir}). Run `pcbkit finalize` first"
    )


# --- the assembly numbers ----------------------------------------------------------


@dataclass(frozen=True)
class Assembly:
    """The assembly values of the order form, per board.

    ``unique_parts`` and ``smd_placements`` are what PCBWay is asked to place;
    ``through_hole_*`` are every through-hole part on the board, whether PCBWay or the
    customer solders it (``self_solder_tht`` says which).
    """

    unique_parts: int
    smd_placements: int
    fine_pitch_parts: int
    fine_pitch_refs: tuple[str, ...]
    through_hole_parts: int
    through_hole_refs: tuple[str, ...]
    self_solder_tht: bool


def assembly_numbers(lines: Sequence[BomLine], self_solder_tht: bool) -> Assembly:
    """Count what the assembly form asks for from the BOM lines."""
    through = [line for line in lines if line.through_hole]
    surface = [line for line in lines if not line.through_hole]
    placed = surface if self_solder_tht else list(lines)
    fine = [line for line in placed if FINE_PITCH.search(line.footprint)]
    return Assembly(
        unique_parts=len(placed),
        smd_placements=sum(line.qty for line in surface),
        fine_pitch_parts=sum(line.qty for line in fine),
        fine_pitch_refs=tuple(
            sorted((r for line in fine for r in line.refs), key=bom.refkey)
        ),
        through_hole_parts=sum(line.qty for line in through),
        through_hole_refs=tuple(
            sorted((r for line in through for r in line.refs), key=bom.refkey)
        ),
        self_solder_tht=self_solder_tht,
    )


# --- the quote ---------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """Everything `pcbkit quote` prints: the files read, the values, the warnings."""

    fab_name: str
    folder: str
    board: BoardSpecs
    fab_qty: int | None
    assembled: int | None
    assembly: Assembly | None
    notes_chars: int | None
    warnings: tuple[str, ...] = field(default=())


def build_quote(
    project: Project,
    *,
    assembled: int | None = None,
    fab_qty: int | None = None,
    self_solder_tht: bool = False,
    notes_chars: int | None = None,
) -> Quote:
    """Read the project's exported fab files and return the values for the order form.

    The assembly section is made only when ``assembled`` is given. A warning is added
    when the Gerber copper differs from ``[stackup] copper_mm`` (the files came from
    another board) and when the fab files are older than the saved board.
    """
    config = project.config
    names = fab_names(config.board.fab_name)
    folder = find_fab_dir(project)
    specs = read_board_specs(folder / names.gerber_zip)
    warnings: list[str] = []
    wanted = config.stackup.copper_mm
    if any(abs(t - wanted) > 1e-6 for t in specs.copper_mm):
        said = ", ".join(f"{t:g}" for t in specs.copper_mm)
        warnings.append(
            f"the Gerbers say {said} mm copper but [stackup] copper_mm is {wanted:g}: "
            "the files are from another board; run `pcbkit finalize`"
        )
    board = project.kicad_dir / f"{config.board.stem}.kicad_pcb"
    zipped = folder / names.gerber_zip
    if board.is_file() and board.stat().st_mtime > zipped.stat().st_mtime:
        warnings.append(
            f"{zipped.name} is older than {board.name}: run `pcbkit finalize` "
            "before you rely on these numbers"
        )
    assembly = None
    if assembled is not None:
        lines = bom.read_csv(folder / names.bom_csv)
        assembly = assembly_numbers(lines, self_solder_tht)
    try:
        shown = str(folder.relative_to(project.root))
    except ValueError:
        shown = str(folder)
    return Quote(
        fab_name=config.board.fab_name,
        folder=shown,
        board=specs,
        fab_qty=fab_qty,
        assembled=assembled,
        assembly=assembly,
        notes_chars=notes_chars,
        warnings=tuple(warnings),
    )


def _mm(value: float) -> str:
    """Return millimetres without trailing zeros: "0.2", "118"."""
    return f"{round(value, 3):g}"


def _mil(value: float) -> str:
    """Return millimetres as mils to one decimal place."""
    return f"{value / MIL_MM:.1f}"


def _copper(thicknesses: Sequence[float]) -> str:
    """Return the outer copper as the form words it: "1 oz (0.035 mm)"."""
    if not thicknesses:
        return "not in the job file"
    if len(set(thicknesses)) == 1:
        return f"{round(thicknesses[0] / OZ_MM, 2):g} oz ({_mm(thicknesses[0])} mm)"
    return ", ".join(f"{round(t / OZ_MM, 2):g} oz ({_mm(t)} mm)" for t in thicknesses)


def _finish(kicad_name: str) -> str:
    """Return the finish as the form words it, with KiCad's name when it differs."""
    if not kicad_name:
        return "not set in the board"
    form = FINISH_NAMES.get(kicad_name, kicad_name)
    return form if form == kicad_name else f'{form} (the board says "{kicad_name}")'


def _pair(track: float | None, space: float | None) -> str:
    """Return the minimum track and spacing as "0.2 / 0.2 mm (7.9 / 7.9 mil)"."""
    if track is None or space is None:
        return "not in the job file"
    mm = f"{_mm(track)} / {_mm(space)} mm"
    return f"{mm} ({_mil(track)} / {_mil(space)} mil)"


def format_quote(quote: Quote) -> str:
    """Return the text `pcbkit quote` prints."""
    board = quote.board
    rows: list[tuple[str, str]] = [
        ("Layers", str(board.layers)),
        ("Board size", f"{_mm(board.width_mm)} x {_mm(board.height_mm)} mm"),
        ("Thickness", f"{_mm(board.thickness_mm)} mm"),
        ("Copper weight", _copper(board.copper_mm)),
        ("Min track / spacing", _pair(board.min_track_mm, board.min_space_mm)),
        (
            "Min hole size",
            "not in the drill files"
            if board.min_hole_mm is None
            else f"{_mm(board.min_hole_mm)} mm ({_mil(board.min_hole_mm)} mil)",
        ),
        ("Surface finish", _finish(board.finish)),
        (
            "Quantity",
            str(quote.fab_qty) if quote.fab_qty else "not given (use --fab-qty N)",
        ),
    ]
    assembly = _assembly_rows(quote.assembly) if quote.assembly is not None else []
    width = max(len(label) for label, _ in rows + assembly)
    out = [f"PCBWay quote values for {quote.fab_name} (files in {quote.folder})", ""]
    out += ["Bare board"] + _table(rows, width)
    if quote.assembly is not None:
        count = quote.assembled
        boards = "1 board" if count == 1 else f"{count} boards"
        out += ["", f"Assembly, {boards} (the counts are per board)"]
        out += _table(assembly, width)
    if quote.notes_chars is not None:
        out += ["", f"Notes: {quote.notes_chars} of {NOTES_LIMIT} characters"]
    for warning in quote.warnings:
        out += ["", f"WARNING: {warning}"]
    return "\n".join(out)


def _assembly_rows(a: Assembly) -> list[tuple[str, str]]:
    """Return the label and value of each assembly row."""
    rows = [("Unique parts", str(a.unique_parts))]
    if a.self_solder_tht:
        rows[0] = ("Unique parts", f"{a.unique_parts} (surface-mount lines only)")
    rows += [
        ("SMD placements", str(a.smd_placements)),
        ("BGA/QFP/QFN parts", str(a.fine_pitch_parts)),
    ]
    if a.fine_pitch_refs:
        rows[-1] = (
            rows[-1][0],
            f"{a.fine_pitch_parts}: {', '.join(a.fine_pitch_refs)}",
        )
    tht = (
        f"{a.through_hole_parts} parts, {len(a.through_hole_refs)} designators: "
        f"{compress_designators(a.through_hole_refs)}"
        if a.through_hole_refs
        else "none"
    )
    if a.self_solder_tht:
        rows.append(("Through-hole parts", "0 for PCBWay (you solder them)"))
        rows.append(("You solder", tht))
    else:
        rows.append(("Through-hole parts", tht))
    return rows


def _table(rows: Sequence[tuple[str, str]], width: int) -> list[str]:
    """Return rows as indented lines with the values lined up at ``width``."""
    return [f"  {label.ljust(width)}  {value}" for label, value in rows]
