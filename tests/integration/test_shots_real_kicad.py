"""Integration: `pcbkit shots` with the real kicad-cli and rsvg-convert.

A project is made around the tiny board (tests/tiny_board.py), with regions declared in
its layout.py, and `pcbkit shots` is run on it once. The pictures are then read back
with Pillow to show that a region really is where layout millimetres say it is: a crop
inside a pad has copper in it, one beside it is white, and the bottom view is not
mirrored. The pictures are judged as white or not white, never by a copper colour:
kicad-cli draws in the colour theme of the PCB editor's settings, which is the user's.
The three 3D renders are made for real too.

These tests need KiCad's own Python (the tiny board is built with pcbnew, and the
command is a tier 2 command), kicad-cli and rsvg-convert. Run them with:

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python they are skipped, for the reason given just below.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)
from PIL import Image  # noqa: E402

from pcbkit import shots  # noqa: E402
from pcbkit.cli import cli  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.kicad import env  # noqa: E402
from tests import tiny_board  # noqa: E402
from tests.board_files import TOML, restored_imports, write_file  # noqa: E402

pytestmark = pytest.mark.kicad

# Regions around R1's pad 1 (x 6.775 to 7.575, y 9.525 to 10.475, with the footprint's
# courtyard line 0.25 mm outside it) and the via at (24.5, 10). Two lie inside the pad
# and in the gap between pad and courtyard, a tenth of a millimetre from either, so a
# shift of more than that shows. Two are on the bottom, where the via's hole is and
# where a mirrored board would have it.
SHOTS = """\
SHOTS = {
    "in_pad": ("top", 6.8, 9.7, 7.1, 10.3, 300),
    "left_of_pad": ("top", 6.55, 9.7, 6.75, 10.3, 300),
    "above_pad": ("top", 6.8, 9.31, 7.1, 9.49, 300),
    "via": ("bottom", 24.2, 9.7, 24.8, 10.3, 300),
    "via_mirrored": ("bottom", 5.2, 9.7, 5.8, 10.3, 300),
}
"""


def is_white(pixel: tuple[int, ...], tol: int = 12) -> bool:
    """Return True if an RGB(A) pixel is white, give or take ``tol`` per channel."""
    return all(channel >= 255 - tol for channel in pixel[:3])


def white_share(image: Image.Image) -> float:
    """Return the fraction of an image's pixels that are white."""
    pixels = list(image.convert("RGB").getdata())
    return sum(1 for pixel in pixels if is_white(pixel)) / len(pixels)


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Run `pcbkit shots` once on a project around the tiny board; return its out/shots.

    The renders are made too, so the one run covers the whole command.
    """
    assert env.find_rsvg_convert() is not None, (
        "rsvg-convert not found: run `pcbkit doctor`"
    )
    root = tmp_path_factory.mktemp("project")
    board = tiny_board.build(tmp_path_factory.mktemp("tiny"))
    write_file(root / "pcbkit.toml", TOML.replace("my_board", "tiny"))
    write_file(root / "layout.py", "W, H = 30, 20\n" + SHOTS)
    (root / "kicad").mkdir()
    shutil.copy(board.pcb, root / "kicad" / "tiny.kicad_pcb")
    with pytest.MonkeyPatch.context() as mp, restored_imports():
        mp.chdir(root)
        result = CliRunner().invoke(cli, ["shots"])
    assert result.exit_code == 0, result.output
    yield root / "out" / "shots"


def test_the_command_writes_an_svg_and_a_png_for_every_region_and_three_renders(
    made: Path,
) -> None:
    names = {p.name for p in made.iterdir()}
    regions = [
        "board_top",
        "board_bottom",
        *re.findall(r'"(\w+)":', SHOTS),
    ]
    for region in regions:
        assert f"{region}.svg" in names and f"{region}.png" in names, region
    assert {"render_iso.png", "render_top.png", "render_bottom.png"} <= names
    assert len(names) == 2 * len(regions) + 3


def test_a_png_is_as_wide_as_asked_and_as_tall_as_its_box(made: Path) -> None:
    assert Image.open(made / "in_pad.png").size == (300, 600)
    assert Image.open(made / "via.png").size == (300, 300)
    width, height = Image.open(made / "board_top.png").size
    assert width == 1800
    # The whole board is as tall as the page kicad-cli fitted to it (see its SVG).
    page = re.search(
        r'viewBox="0.0000 0.0000 ([\d.]+) ([\d.]+)"',
        (made / "board_top.svg").read_text(encoding="utf-8"),
    )
    assert page
    assert height == pytest.approx(1800 * float(page[2]) / float(page[1]), abs=1)


def test_a_region_inside_a_pad_is_copper_from_edge_to_edge(made: Path) -> None:
    """Show that layout millimetres are the SVG's units: the pad is where it is."""
    assert white_share(Image.open(made / "in_pad.png")) < 0.02


def test_regions_in_the_gap_around_the_pad_have_no_copper_in_them(made: Path) -> None:
    """Show the box is where it says, to a tenth of a millimetre, on both axes."""
    for name in ("left_of_pad", "above_pad"):
        assert white_share(Image.open(made / f"{name}.png")) > 0.97, name


def test_the_bottom_view_has_the_vias_hole_where_the_layout_has_the_via(
    made: Path,
) -> None:
    """Keep the bottom view unmirrored: the hole is at x = 24.5, not at 30 - 24.5."""
    hole = Image.open(made / "via.png").convert("RGB")
    assert is_white(hole.getpixel((150, 150)))  # the drill, in the middle
    assert not is_white(hole.getpixel((10, 10)))  # the copper round it
    assert not is_white(hole.getpixel((290, 290)))
    other = Image.open(made / "via_mirrored.png").convert("RGB")
    assert white_share(other) < 0.01  # no hole where a mirrored board would have it
    assert Counter(other.getdata()).most_common(1)[0][1] > 0.95 * 300 * 300


def test_the_svg_of_a_region_is_the_board_narrowed_to_its_box(made: Path) -> None:
    svg = (made / "in_pad.svg").read_text(encoding="utf-8")
    assert 'viewBox="6.8 9.7 0.3 0.6"' in svg
    assert 'width="0.3mm" height="0.6mm"' in svg
    whole = (made / "board_top.svg").read_text(encoding="utf-8")
    assert 'viewBox="0.0000 0.0000' in whole


def test_the_three_renders_are_real_pngs_of_about_the_requested_shape(
    made: Path,
) -> None:
    """Check shape, not pixels: kicad-cli returns a little less than it is asked for.

    The command lines are pinned by a unit test; this shows that they make pictures.
    """
    for name, (width, height) in {
        "render_iso.png": (2400, 1600),
        "render_top.png": (2400, 1500),
        "render_bottom.png": (2400, 1500),
    }.items():
        image = Image.open(made / name)
        assert image.format == "PNG"
        w, h = image.size
        assert 0.9 * width <= w <= 1.1 * width, name
        assert 0.9 * height <= h <= 1.1 * height, name
        assert abs(w / h - width / height) < 0.05 * (width / height), name
        assert len(set(image.convert("RGB").getdata())) > 50, f"{name} looks blank"


def test_a_boxs_origin_is_the_outlines_corner_not_layout_zero(tmp_path: Path) -> None:
    """Show the condition the docs state: the SVG starts where the outline starts.

    Move the whole outline 5 mm right and 3 mm down and the pad of R1 is where the box
    (6.8, 9.7)-(7.1, 10.3) minus that shift says, not where layout.py's coordinates do.
    If KiCad ever changes this, the docs and this test are what to revisit.
    """
    board = tiny_board.build(tmp_path / "tiny")
    moved = pcbnew.LoadBoard(str(board.pcb))
    for item in moved.GetDrawings():
        if item.GetLayer() == pcbnew.Edge_Cuts:
            item.Move(pcbnew.VECTOR2I(kb.mm(5), kb.mm(3)))
    pcbnew.SaveBoard(str(board.pcb), moved)
    regions = shots.parse_regions(
        {
            "at_layout_coordinates": ("top", 6.8, 9.7, 7.1, 10.3, 300),
            "shifted_by_the_outline": ("top", 1.8, 6.7, 2.1, 7.3, 300),
        }
    )
    out = tmp_path / "out"
    shots.take_shots(board.pcb, out, regions, render=False)
    where_layout_says = Image.open(out / "at_layout_coordinates.png")
    where_the_pad_is = Image.open(out / "shifted_by_the_outline.png")
    assert white_share(where_layout_says) > 0.97
    assert white_share(where_the_pad_is) < 0.02


def test_a_missing_rsvg_convert_stops_the_command_before_it_exports_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the message a user gets, with the real kicad-cli on the machine."""
    monkeypatch.setattr(env, "find_rsvg_convert", lambda: None)
    board = tiny_board.build(tmp_path / "tiny")
    with pytest.raises(shots.ShotsError, match="brew install librsvg"):
        shots.take_shots(board.pcb, tmp_path / "out", shots.board_regions())
    assert not (tmp_path / "out").exists()
