"""Review shots of a board: crops of named regions, as SVG and PNG, and 3D renders.

``take_shots`` exports the board's top and bottom views as SVG with kicad-cli, cuts
each named region out of them, turns every cut into a PNG with rsvg-convert, and
renders the whole board in 3D. It is what to run before a review, to look at a corner of
the board at a size where the copper can be read.

Regions are the project's to name. ``SHOTS`` in layout.py maps a region's name to
``(side, x0, y0, x1, y1)``, in layout millimetres (see docs/project-interface.md), and
the two whole-board shots ``board_top`` and ``board_bottom`` are always there.

The SVG that kicad-cli writes with ``--fit-page-to-board`` is in millimetres from the
board's top-left corner, the same origin as layout coordinates, so a region is cut out
by narrowing the SVG's ``viewBox`` to it: no drawing is redone and nothing is mirrored,
so the bottom view uses the same x as the top view.

Nothing here needs pcbnew: only kicad-cli and rsvg-convert, which run through
``pcbkit.kicad.cli`` and ``pcbkit.kicad.env`` so unit tests can fake them.
"""

from __future__ import annotations

import math
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from pcbkit.kicad import cli, env
from pcbkit.project import NAME_PATTERN, Project, import_optional_project_module

SIDES = ("top", "bottom")

# What each SVG shows: the copper, silkscreen, courtyards, fab outlines and the board
# edge of one side.
SVG_LAYERS = {
    "top": ("F.Cu", "F.SilkS", "F.CrtYd", "Edge.Cuts", "F.Fab"),
    "bottom": ("B.Cu", "B.SilkS", "B.CrtYd", "Edge.Cuts", "B.Fab"),
}

# PNG widths in pixels: a region's, and a whole board's.
REGION_WIDTH_PX = 1600
BOARD_WIDTH_PX = 1800
MIN_WIDTH_PX, MAX_WIDTH_PX = 100, 10000

# Seconds to wait for rsvg-convert on one image.
TIMEOUT_RSVG = 120.0

# Names the shots use for themselves: the whole-board shots, and the 3D renders (whose
# files are called render_<view>.png), so a region cannot overwrite either.
BOARD_SHOTS = ("board_top", "board_bottom")
RESERVED_PREFIX = "render_"


class ShotsError(click.ClickException):
    """Report a problem with the shots: a bad region, a missing tool, a failed image."""


@dataclass(frozen=True)
class Region:
    """A view to save: one side of the board, cut to ``box`` (or all of it).

    ``box`` is (x0, y0, x1, y1) in layout millimetres, or None for the whole board.
    ``width_px`` is the width of the PNG; its height follows from the box.
    """

    name: str
    side: str
    box: tuple[float, float, float, float] | None
    width_px: int


@dataclass(frozen=True)
class RenderView:
    """One 3D render: which side, from which angle, how big and how close."""

    name: str
    side: str
    rotate: tuple[float, float, float] | None
    width: int
    height: int
    zoom: float
    quality: str = "high"


# The three renders a finished board gets: an angled view and the two flat ones.
RENDER_VIEWS = (
    RenderView("iso", "top", (-40.0, 0.0, -20.0), 2400, 1600, 1.1),
    RenderView("top", "top", None, 2400, 1500, 1.25),
    RenderView("bottom", "bottom", None, 2400, 1500, 1.25),
)


@dataclass(frozen=True)
class ShotsResult:
    """What a run wrote: the folder, the regions, the renders and every file."""

    out_dir: Path
    regions: tuple[str, ...]
    renders: tuple[Path, ...]
    files: tuple[Path, ...]


# --- regions ----------------------------------------------------------------------


def board_regions() -> dict[str, Region]:
    """Return the two whole-board shots every project gets."""
    return {
        "board_top": Region("board_top", "top", None, BOARD_WIDTH_PX),
        "board_bottom": Region("board_bottom", "bottom", None, BOARD_WIDTH_PX),
    }


def _number(value: object) -> float | None:
    """Return ``value`` as a float if it is a finite int or float, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def parse_regions(raw: object, source: str = "layout.py") -> dict[str, Region]:
    """Validate a ``SHOTS`` mapping and return its regions, in the order it lists them.

    Each value is ``(side, x0, y0, x1, y1)`` or ``(side, x0, y0, x1, y1, width_px)``:
    ``side`` is "top" or "bottom", the corners are numbers with x0 < x1 and y0 < y1, and
    ``width_px`` is a whole number of pixels. Raise ShotsError naming ``source``, and
    every problem at once.
    """
    if not isinstance(raw, Mapping):
        raise ShotsError(
            f"{source}: SHOTS should be a dict of region name to "
            f"(side, x0, y0, x1, y1), got {type(raw).__name__}"
        )
    problems: list[str] = []
    regions: dict[str, Region] = {}
    for name, entry in raw.items():
        where = f"SHOTS[{name!r}]"
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            problems.append(
                f"{where}: the name is used in file names: use letters, digits, '_', "
                "'-' and '.', starting with a letter, digit or '_'"
            )
            continue
        if name.startswith(RESERVED_PREFIX):
            problems.append(
                f"{where}: names starting with {RESERVED_PREFIX!r} are kept for the "
                "3D renders"
            )
            continue
        if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence):
            problems.append(f"{where}: expected (side, x0, y0, x1, y1), got {entry!r}")
            continue
        if len(entry) not in (5, 6):
            problems.append(
                f"{where}: expected (side, x0, y0, x1, y1) and optionally a width in "
                f"pixels, got {len(entry)} values"
            )
            continue
        side = entry[0]
        if side not in SIDES:
            problems.append(f"{where}: side should be 'top' or 'bottom', got {side!r}")
            continue
        corners = [_number(value) for value in entry[1:5]]
        if any(corner is None for corner in corners):
            problems.append(f"{where}: x0, y0, x1 and y1 should be numbers: {entry!r}")
            continue
        x0, y0, x1, y1 = (float(corner) for corner in corners)  # none is None here
        if not (x0 < x1 and y0 < y1):
            problems.append(f"{where}: needs x0 < x1 and y0 < y1, got {entry[1:5]!r}")
            continue
        width = REGION_WIDTH_PX
        if len(entry) == 6:
            given = entry[5]
            if (
                isinstance(given, bool)
                or not isinstance(given, int)
                or not MIN_WIDTH_PX <= given <= MAX_WIDTH_PX
            ):
                problems.append(
                    f"{where}: the width should be a whole number of pixels from "
                    f"{MIN_WIDTH_PX} to {MAX_WIDTH_PX}, got {given!r}"
                )
                continue
            width = given
        regions[name] = Region(name, side, (x0, y0, x1, y1), width)
    if problems:
        count = f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"
        lines = "".join(f"\n  {problem}" for problem in problems)
        raise ShotsError(f"{source}: {count} in SHOTS{lines}")
    return regions


def load_regions(proj: Project) -> dict[str, Region]:
    """Return the project's regions: its ``SHOTS``, after the whole-board shots.

    A region the project declares under a whole-board name replaces that shot. A
    project with no layout.py, or none with a ``SHOTS``, gets just the whole-board
    shots.
    """
    regions = board_regions()
    layout = import_optional_project_module(proj.root, "layout")
    declared = getattr(layout, "SHOTS", None)
    if declared is not None:
        regions.update(parse_regions(declared, str(proj.root / "layout.py")))
    return regions


def select_regions(
    regions: Mapping[str, Region], names: Sequence[str]
) -> dict[str, Region]:
    """Return the regions called ``names`` (all if none); raise on an unknown name."""
    if not names:
        return dict(regions)
    unknown = [name for name in names if name not in regions]
    if unknown:
        known = ", ".join(regions)
        raise ShotsError(
            f"no region called {', '.join(map(repr, unknown))}; "
            f"the regions are: {known}"
        )
    return {name: regions[name] for name in regions if name in names}


# --- cutting an SVG ---------------------------------------------------------------

_ROOT_TAG = re.compile(r"<svg\b[^>]*>", re.DOTALL)
_VIEWBOX = re.compile(r'viewBox="[^"]*"')
_SIZE_MM = re.compile(r'width="[^"]*mm"(\s+)height="[^"]*mm"')


def _g(value: float) -> str:
    """Return a number the short way: 88 for 88.0, and no float noise on 80.2."""
    return format(value, ".10g")


def crop_svg(svg: str, box: tuple[float, float, float, float]) -> str:
    """Return ``svg`` showing only ``box`` (x0, y0, x1, y1), at the box's size in mm.

    Only the root element changes: its ``viewBox``, ``width`` and ``height``. Raise
    ShotsError if the text has no ``<svg>`` root with a viewBox and a size in mm, which
    is what kicad-cli writes.
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    root = _ROOT_TAG.search(svg)
    if root is None:
        raise ShotsError("cannot crop: the text has no <svg> element")
    tag, views = _VIEWBOX.subn(
        f'viewBox="{_g(x0)} {_g(y0)} {_g(width)} {_g(height)}"', root.group(0), count=1
    )
    tag, sizes = _SIZE_MM.subn(
        lambda found: f'width="{_g(width)}mm"{found.group(1)}height="{_g(height)}mm"',
        tag,
        count=1,
    )
    if views != 1 or sizes != 1:
        raise ShotsError(
            "cannot crop: the <svg> element has no viewBox and no width and height in "
            f"mm, as kicad-cli writes them: {root.group(0)[:200]}"
        )
    return svg[: root.start()] + tag + svg[root.end() :]


def rsvg_convert() -> str:
    """Return the path of rsvg-convert, or raise ShotsError saying how to get it."""
    tool = env.find_rsvg_convert()
    if tool is None:
        raise ShotsError(
            "rsvg-convert not found: pcbkit makes PNGs from SVG with it. "
            "Install librsvg (brew install librsvg), or see `pcbkit doctor`."
        )
    return tool.path


def svg_to_png(svg: Path, png: Path, width_px: int, tool: str | None = None) -> None:
    """Draw ``svg`` as a PNG ``width_px`` wide on a white background."""
    tool = tool or rsvg_convert()
    if png.is_file():
        png.unlink()  # so one that exists afterwards was written by this run
    done = env._run(
        [tool, "-w", str(width_px), "-b", "white", str(svg), "-o", str(png)],
        timeout=TIMEOUT_RSVG,
    )
    if done.returncode != 0:
        reason = done.error or f"exit {done.returncode}"
        raise ShotsError(
            f"rsvg-convert failed on {svg} ({reason})\n{done.output}".rstrip()
        )
    if not png.is_file():
        raise ShotsError(f"rsvg-convert ran but wrote nothing at {png}")


# --- the shots --------------------------------------------------------------------


def render_views(
    pcb: Path,
    out_dir: Path,
    prefix: str = "",
    views: Sequence[RenderView] = RENDER_VIEWS,
) -> list[Path]:
    """Render ``pcb`` in 3D, one PNG per view, to ``out_dir/<prefix>render_<view>.png``.

    The defaults are the three renders a finished board gets (``iso``, ``top`` and
    ``bottom``); a fab export that wants them next to its PDFs gives a ``prefix``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for view in views:
        out = out_dir / f"{prefix}render_{view.name}.png"
        cli.render_3d(
            pcb,
            out,
            side=view.side,
            rotate=view.rotate,
            width=view.width,
            height=view.height,
            quality=view.quality,
            zoom=view.zoom,
        )
        written.append(out)
    return written


def take_shots(
    pcb: Path,
    out_dir: Path,
    regions: Mapping[str, Region],
    *,
    render: bool = True,
) -> ShotsResult:
    """Save each region of ``pcb`` as ``<name>.svg`` and ``<name>.png`` in ``out_dir``.

    With ``render`` the three 3D renders are saved there too. Check for rsvg-convert
    before anything is exported, so a missing tool costs nothing.
    """
    tool = rsvg_convert()
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = [side for side in SIDES if any(r.side == side for r in regions.values())]
    files: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="pcbkit-shots-") as scratch:
        exported: dict[str, str] = {}
        for side in wanted:
            path = Path(scratch) / f"{side}.svg"
            cli.export_svg(pcb, path, SVG_LAYERS[side])
            exported[side] = path.read_text(encoding="utf-8")
        for region in regions.values():
            whole = exported[region.side]
            svg = whole if region.box is None else crop_svg(whole, region.box)
            svg_path = out_dir / f"{region.name}.svg"
            svg_path.write_text(svg, encoding="utf-8")
            png_path = out_dir / f"{region.name}.png"
            svg_to_png(svg_path, png_path, region.width_px, tool)
            files += [svg_path, png_path]
    renders = render_views(pcb, out_dir) if render else []
    return ShotsResult(out_dir, tuple(regions), tuple(renders), tuple(files + renders))


def format_result(result: ShotsResult, root: Path | None = None) -> str:
    """Return the lines `pcbkit shots` prints: what was made, and where."""
    where = result.out_dir
    if root is not None and root in (where, *where.parents):
        where = where.relative_to(root)
    lines = [f"{len(result.regions)} region shots in {where}:"]
    lines += [f"  {name}.png" for name in result.regions]
    if result.renders:
        lines.append(f"{len(result.renders)} 3D renders:")
        lines += [f"  {path.name}" for path in result.renders]
    else:
        lines.append("3D renders skipped.")
    return "\n".join(lines)
