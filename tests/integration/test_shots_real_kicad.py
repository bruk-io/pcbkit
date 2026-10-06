"""Integration: `pcbkit shots` with the real kicad-cli and rsvg-convert.

A project is made around the tiny board (tests/tiny_board.py), with regions declared in
its layout.py, and `pcbkit shots` is run on it once. The pictures are then read back
with Pillow to show that a region really is where layout millimetres say it is: a crop
around a pad is copper-coloured, one beside it is white, and the bottom view is not
mirrored. The three 3D renders are made for real too.

These tests need KiCad's own Python (the tiny board is built with pcbnew, and the
command is a tier 2 command), kicad-cli and rsvg-convert. Run them with:

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python they are skipped, for the reason given just below.
"""

from __future__ import annotations

import re
import shutil
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
from pcbkit.kicad import env  # noqa: E402
from tests import tiny_board  # noqa: E402
from tests.board_files import TOML, restored_imports, write_file  # noqa: E402

pytestmark = pytest.mark.kicad

# KiCad's default copper colours in an SVG: F.Cu and B.Cu.
RED = (0xC8, 0x34, 0x34)
BLUE = (0x4D, 0x7F, 0xC4)
WHITE = (255, 255, 255)

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


def close(pixel: tuple[int, ...], colour: tuple[int, int, int], tol: int = 12) -> bool:
    """Return True if an RGB(A) pixel is within ``tol`` of ``colour`` per channel."""
    return all(abs(a - b) <= tol for a, b in zip(pixel[:3], colour))


def share(image: Image.Image, colour: tuple[int, int, int]) -> float:
    """Return the fraction of an image's pixels that are ``colour``."""
    rgb = image.convert("RGB")
    pixels = list(rgb.getdata())
    return sum(1 for p in pixels if close(p, colour)) / len(pixels)


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
    assert height == pytest.approx(1800 * 19.9898 / 29.9974, abs=1)  # the whole board


def test_a_region_inside_a_pad_is_copper_from_edge_to_edge(made: Path) -> None:
    """Show that layout millimetres are the SVG's units: the pad is where it is."""
    assert share(Image.open(made / "in_pad.png"), RED) > 0.98


def test_regions_in_the_gap_around_the_pad_have_no_copper_in_them(made: Path) -> None:
    """Show the box is where it says, to a tenth of a millimetre, on both axes."""
    for name in ("left_of_pad", "above_pad"):
        image = Image.open(made / f"{name}.png")
        assert share(image, RED) < 0.01, name
        assert share(image, WHITE) > 0.97, name


def test_the_bottom_view_has_the_vias_hole_where_the_layout_has_the_via(
    made: Path,
) -> None:
    """Keep the bottom view unmirrored: the hole is at x = 24.5, not at 30 - 24.5."""
    hole = Image.open(made / "via.png").convert("RGB")
    assert close(hole.getpixel((150, 150)), WHITE)  # the drill, in the middle
    assert close(hole.getpixel((10, 10)), BLUE) and close(
        hole.getpixel((290, 290)), BLUE
    )
    other = Image.open(made / "via_mirrored.png").convert("RGB")
    assert share(other, WHITE) < 0.01
    assert share(other, BLUE) > 0.95


def test_the_svg_of_a_region_is_the_board_narrowed_to_its_box(made: Path) -> None:
    svg = (made / "in_pad.svg").read_text(encoding="utf-8")
    assert 'viewBox="6.8 9.7 0.3 0.6"' in svg
    assert 'width="0.3mm" height="0.6mm"' in svg
    whole = (made / "board_top.svg").read_text(encoding="utf-8")
    assert 'viewBox="0.0000 0.0000' in whole


def test_the_three_renders_are_real_pngs_of_the_requested_size(made: Path) -> None:
    for name, (width, height) in {
        "render_iso.png": (2400, 1600),
        "render_top.png": (2400, 1500),
        "render_bottom.png": (2400, 1500),
    }.items():
        image = Image.open(made / name)
        assert image.format == "PNG"
        # kicad-cli trims a few pixels off the size it is asked for.
        assert abs(image.size[0] - width) <= 0.02 * width, name
        assert abs(image.size[1] - height) <= 0.02 * height, name
        assert len(set(image.convert("RGB").getdata())) > 50, f"{name} looks blank"


def test_a_missing_rsvg_convert_stops_the_command_before_it_exports_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the message a user gets, with the real kicad-cli on the machine."""
    monkeypatch.setattr(env, "find_rsvg_convert", lambda: None)
    board = tiny_board.build(tmp_path / "tiny")
    with pytest.raises(shots.ShotsError, match="brew install librsvg"):
        shots.take_shots(board.pcb, tmp_path / "out", shots.board_regions())
    assert not (tmp_path / "out").exists()
