"""Unit tests for pcbkit.shots and `pcbkit shots`, with the two tools faked.

kicad-cli and rsvg-convert are faked and nothing else is: the fakes record the command
line they were given and write the files the real tools would (the SVG is a real
kicad-cli export of a tiny board, tests/fixtures/shots/tiny_top.svg). Everything pcbkit
does with that, from the command lines it builds to the way it cuts the SVG, is the
real code. The real tools run in tests/integration/test_shots_real_kicad.py.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit import shots
from pcbkit.cli import cli
from pcbkit.kicad import env
from pcbkit.kicad.env import Run
from pcbkit.project import load_project
from pcbkit.shots import Region, ShotsError
from tests.board_files import TOML, restored_imports, write_file
from tests.fake_machine import FakeMachine

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "shots" / "tiny_top.svg"
SVG = FIXTURE.read_text(encoding="utf-8")
PNG = b"\x89PNG\r\n\x1a\nfake"


def root_tag(svg: str) -> str:
    """Return the <svg ...> start tag of an SVG text."""
    found = re.search(r"<svg\b[^>]*>", svg, flags=re.DOTALL)
    assert found
    return found.group(0)


# --- cutting an SVG ----------------------------------------------------------------


def test_crop_svg_narrows_the_viewbox_and_the_size_to_the_box() -> None:
    cropped = shots.crop_svg(SVG, (10.0, 5.0, 20.0, 12.5))
    tag = root_tag(cropped)
    assert 'viewBox="10 5 10 7.5"' in tag
    assert 'width="10mm" height="7.5mm"' in tag
    assert 'viewBox="0.0000' not in cropped


def test_crop_svg_changes_nothing_but_the_root_element() -> None:
    cropped = shots.crop_svg(SVG, (10.0, 5.0, 20.0, 12.5))
    start, end = SVG.index("<svg"), SVG.index(">", SVG.index("<svg")) + 1
    assert cropped[:start] == SVG[:start]
    assert cropped[cropped.index(">", cropped.index("<svg")) + 1 :] == SVG[end:]
    assert 'xmlns="http://www.w3.org/2000/svg"' in root_tag(cropped)
    assert 'version="1.1"' in root_tag(cropped)


@pytest.mark.parametrize(
    ("box", "view", "size"),
    [
        ((30, 0, 118, 20), "30 0 88 20", 'width="88mm" height="20mm"'),
        ((0, 0, 118.0, 68.0), "0 0 118 68", 'width="118mm" height="68mm"'),
        ((20.1, 0.5, 100.3, 1.5), "20.1 0.5 80.2 1", 'width="80.2mm" height="1mm"'),
        ((-2.5, 0, 2.5, 5), "-2.5 0 5 5", 'width="5mm" height="5mm"'),
    ],
)
def test_crop_svg_writes_whole_numbers_whole_and_no_float_noise(
    box: tuple[float, float, float, float], view: str, size: str
) -> None:
    """Write 88 for 88.0 and 80.2 for 100.3 - 20.1, not 80.19999999999999."""
    tag = root_tag(shots.crop_svg(SVG, box))
    assert f'viewBox="{view}"' in tag
    assert size in tag


def test_crop_svg_only_changes_the_root_when_another_element_has_a_viewbox() -> None:
    marker = '<marker id="m" viewBox="0 0 10 10" width="3mm" height="3mm"/>'
    with_marker = SVG.replace("<desc>", marker + "<desc>", 1)
    cropped = shots.crop_svg(with_marker, (1, 2, 3, 4))
    assert marker in cropped
    assert 'viewBox="1 2 2 2"' in root_tag(cropped)


@pytest.mark.parametrize(
    "text",
    [
        "not an svg at all",
        '<svg width="10mm" height="10mm">',
        '<svg viewBox="0 0 10 10">',
        '<svg width="10px" height="10px" viewBox="0 0 10 10">',
    ],
    ids=["no-svg", "no-viewbox", "no-size", "size-not-in-mm"],
)
def test_crop_svg_refuses_what_kicad_cli_does_not_write(text: str) -> None:
    with pytest.raises(ShotsError, match="cannot crop"):
        shots.crop_svg(text, (0, 0, 1, 1))


# --- the regions the project declares ------------------------------------------------


def test_parse_regions_reads_five_values_and_an_optional_width() -> None:
    regions = shots.parse_regions(
        {
            "left": ("top", 0, 0, 30, 20),
            "right": ["bottom", 30.5, 0.25, 60, 20, 2400],
        }
    )
    assert list(regions) == ["left", "right"]
    assert regions["left"] == Region("left", "top", (0.0, 0.0, 30.0, 20.0), 1600)
    assert regions["right"] == Region("right", "bottom", (30.5, 0.25, 60.0, 20.0), 2400)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (("left", 0, 0, 30, 20), "side should be 'top' or 'bottom', got 'left'"),
        (("top", 30, 0, 30, 20), "needs x0 < x1 and y0 < y1"),
        (("top", 0, 20, 30, 10), "needs x0 < x1 and y0 < y1"),
        (("top", 0, 0, "30", 20), "should be numbers"),
        (("top", 0, 0, True, 20), "should be numbers"),
        (("top", 0, 0, float("inf"), 20), "should be numbers"),
        (("top", 0, 0, float("nan"), 20), "should be numbers"),
        (("top", 0, 0, 30), "got 4 values"),
        (("top", 0, 0, 30, 20, 1600, 1), "got 7 values"),
        ("top 0 0 30 20", "expected (side, x0, y0, x1, y1)"),
        (7, "expected (side, x0, y0, x1, y1)"),
        (("top", 0, 0, 30, 20, 50), "from 100 to 10000"),
        (("top", 0, 0, 30, 20, 20000), "from 100 to 10000"),
        (("top", 0, 0, 30, 20, 1600.0), "whole number of pixels"),
        (("top", 0, 0, 30, 20, True), "whole number of pixels"),
    ],
)
def test_parse_regions_rejects_each_mistake_with_its_name_and_the_file(
    entry: object, message: str
) -> None:
    with pytest.raises(ShotsError) as caught:
        shots.parse_regions({"area": entry}, "my/layout.py")
    text = caught.value.message
    assert text.startswith("my/layout.py: 1 problem in SHOTS")
    assert "SHOTS['area']" in text
    assert message in text


@pytest.mark.parametrize(
    "name", ["a b", "a/b", "-a", ".a", "", "render_iso", "render_x", 5]
)
def test_parse_regions_rejects_names_that_would_not_make_good_files(name: Any) -> None:
    with pytest.raises(ShotsError, match="1 problem"):
        shots.parse_regions({name: ("top", 0, 0, 1, 1)})


def test_parse_regions_accepts_names_with_dots_dashes_and_digits() -> None:
    names = ["a.b", "a-b", "9lives", "_x", "board_top", "renders"]
    regions = shots.parse_regions(dict.fromkeys(names, ("top", 0, 0, 1, 1)))
    assert list(regions) == names


def test_parse_regions_reports_every_problem_at_once() -> None:
    with pytest.raises(ShotsError) as caught:
        shots.parse_regions(
            {
                "ok": ("top", 0, 0, 1, 1),
                "one": ("up", 0, 0, 1, 1),
                "two": ("top", 1, 0, 0, 1),
                "bad name": ("top", 0, 0, 1, 1),
            }
        )
    lines = caught.value.message.splitlines()
    assert lines[0] == "layout.py: 3 problems in SHOTS"
    assert len(lines) == 4


@pytest.mark.parametrize("raw", [[("top", 0, 0, 1, 1)], "top", 5, ("top", 0, 0, 1, 1)])
def test_parse_regions_wants_a_dict(raw: object) -> None:
    with pytest.raises(ShotsError, match="SHOTS should be a dict"):
        shots.parse_regions(raw)


def test_an_empty_shots_is_fine() -> None:
    assert shots.parse_regions({}) == {}


@pytest.fixture
def project_root(tmp_path: Path) -> Iterator[Path]:
    """Return a folder with a pcbkit.toml, and undo its modules after the test."""
    with restored_imports():
        root = (tmp_path / "project").resolve()
        write_file(root / "pcbkit.toml", TOML)
        yield root


def test_load_regions_without_a_layout_gives_the_two_whole_board_shots(
    project_root: Path,
) -> None:
    regions = shots.load_regions(load_project(project_root))
    assert list(regions) == ["board_top", "board_bottom"]
    assert [(r.side, r.box, r.width_px) for r in regions.values()] == [
        ("top", None, 1800),
        ("bottom", None, 1800),
    ]


def test_load_regions_adds_the_projects_regions_after_the_board_shots(
    project_root: Path,
) -> None:
    write_file(
        project_root / "layout.py",
        """
        W, H = 30, 20
        SHOTS = {
            "corner": ("top", 0, 0, 10, 10),
            "corner_back": ("bottom", 0, 0, 10, 10, 800),
        }
        """,
    )
    regions = shots.load_regions(load_project(project_root))
    assert list(regions) == ["board_top", "board_bottom", "corner", "corner_back"]
    assert regions["corner_back"].width_px == 800


def test_a_declared_board_top_replaces_the_automatic_one(project_root: Path) -> None:
    write_file(
        project_root / "layout.py",
        'SHOTS = {"board_top": ("top", 0, 0, 30, 20, 3000)}\n',
    )
    regions = shots.load_regions(load_project(project_root))
    assert list(regions) == ["board_top", "board_bottom"]
    assert regions["board_top"].box == (0.0, 0.0, 30.0, 20.0)
    assert regions["board_top"].width_px == 3000


def test_a_layout_without_shots_gives_just_the_board_shots(project_root: Path) -> None:
    write_file(project_root / "layout.py", "W, H = 30, 20\nP = {}\n")
    assert list(shots.load_regions(load_project(project_root))) == [
        "board_top",
        "board_bottom",
    ]


def test_a_bad_shots_in_layout_py_is_an_error_that_names_the_file(
    project_root: Path,
) -> None:
    write_file(project_root / "layout.py", 'SHOTS = {"x": ("side", 0, 0, 1, 1)}\n')
    with pytest.raises(ShotsError) as caught:
        shots.load_regions(load_project(project_root))
    assert str(project_root / "layout.py") in caught.value.message
    assert "side should be 'top' or 'bottom'" in caught.value.message


def test_select_regions_keeps_declared_order_and_names_the_strangers() -> None:
    regions = shots.parse_regions(
        dict.fromkeys(("a", "b", "c"), ("top", 0, 0, 1, 1)), "layout.py"
    )
    assert shots.select_regions(regions, []) == regions
    assert list(shots.select_regions(regions, ["c", "a"])) == ["a", "c"]
    with pytest.raises(ShotsError) as caught:
        shots.select_regions(regions, ["a", "nope", "zip"])
    assert caught.value.message == (
        "no region called 'nope', 'zip'; the regions are: a, b, c"
    )


# --- the two tools, faked ---------------------------------------------------------


@dataclass
class Tools:
    """What the fake kicad-cli and rsvg-convert were asked to do."""

    kicad: str
    rsvg: str | None
    calls: list[list[str]] = field(default_factory=list)

    def of(self, tool: str) -> list[list[str]]:
        """Return the commands (after the tool's name) that ``tool`` ran."""
        return [c[1:] for c in self.calls if c[0] == tool]


def install_tools(
    monkeypatch: pytest.MonkeyPatch,
    machine: FakeMachine,
    *,
    rsvg: bool = True,
    rsvg_run: Callable[[list[str]], Run] | None = None,
) -> Tools:
    """Put a fake kicad-cli (and rsvg-convert) on the fake PATH and make them work."""
    kicad_path = str(
        machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
    )
    rsvg_path = (
        str(machine.exe(machine.usr_bin / "rsvg-convert", "", on_path="rsvg-convert"))
        if rsvg
        else None
    )
    tools = Tools(kicad_path, rsvg_path)

    def target(args: list[str]) -> Path:
        return Path(args[args.index("-o") + 1])

    def run(args: list[str], timeout: float = 20.0) -> Run:
        if args == [kicad_path, "--version"]:
            return Run(0, "10.0.6\n")
        if args == [rsvg_path, "--version"]:
            return Run(0, "rsvg-convert version 2.60.0\n")
        tools.calls.append(list(args))
        if args[0] == kicad_path and args[1:4] == ["pcb", "export", "svg"]:
            target(args).write_text(SVG, encoding="utf-8")
        elif args[0] == kicad_path and args[1:3] == ["pcb", "render"]:
            target(args).write_bytes(PNG)
        elif args[0] == rsvg_path:
            if rsvg_run is not None:
                return rsvg_run(args)
            Path(args[args.index("-o") + 1]).write_bytes(PNG)
        else:
            raise AssertionError(f"unexpected command: {args}")
        return Run(0, "")

    monkeypatch.setattr(env, "_run", run)
    return tools


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, machine: FakeMachine) -> Tools:
    """Return the fake tools, installed and working."""
    return install_tools(monkeypatch, machine)


def regions_for_test() -> dict[str, Region]:
    """Return two top regions, one bottom region and the whole-board shots."""
    declared = shots.parse_regions(
        {
            "corner": ("top", 0, 0, 10, 10),
            "wide": ("top", 5, 5, 25, 15, 800),
            "back": ("bottom", 0, 0, 10, 10),
        }
    )
    return {**shots.board_regions(), **declared}


# --- taking shots -------------------------------------------------------------------


def test_take_shots_exports_each_side_once_and_makes_an_svg_and_png_per_region(
    tools: Tools, tmp_path: Path
) -> None:
    pcb = tmp_path / "b.kicad_pcb"
    out = tmp_path / "shots"
    result = shots.take_shots(pcb, out, regions_for_test(), render=False)

    exports = tools.of(tools.kicad)
    assert len(exports) == 2
    layers = [e[e.index("--layers") + 1] for e in exports]
    assert layers == [
        "F.Cu,F.SilkS,F.CrtYd,Edge.Cuts,F.Fab",
        "B.Cu,B.SilkS,B.CrtYd,Edge.Cuts,B.Fab",
    ]
    for export in exports:
        assert export[:3] == ["pcb", "export", "svg"]
        assert "--fit-page-to-board" in export and export[-1] == str(pcb)

    assert result.regions == ("board_top", "board_bottom", "corner", "wide", "back")
    assert result.renders == ()
    assert sorted(p.name for p in out.iterdir()) == sorted(
        f"{n}.{ext}" for n in result.regions for ext in ("svg", "png")
    )
    assert tuple(result.files) == tuple(
        out / f"{n}.{ext}" for n in result.regions for ext in ("svg", "png")
    )


def test_each_region_is_the_export_cropped_to_its_box(
    tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "shots"
    shots.take_shots(tmp_path / "b.kicad_pcb", out, regions_for_test(), render=False)
    assert (out / "board_top.svg").read_text(encoding="utf-8") == SVG
    assert (out / "board_bottom.svg").read_text(encoding="utf-8") == SVG
    assert (out / "corner.svg").read_text(encoding="utf-8") == shots.crop_svg(
        SVG, (0, 0, 10, 10)
    )
    assert 'viewBox="5 5 20 10"' in (out / "wide.svg").read_text(encoding="utf-8")


def test_each_png_is_rsvg_convert_on_the_svg_at_the_regions_width(
    tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "shots"
    shots.take_shots(tmp_path / "b.kicad_pcb", out, regions_for_test(), render=False)
    assert tools.rsvg is not None
    converts = tools.of(tools.rsvg)
    assert converts == [
        [
            "-w",
            str(w),
            "-b",
            "white",
            str(out / f"{n}.svg"),
            "-o",
            str(out / f"{n}.png"),
        ]
        for n, w in [
            ("board_top", 1800),
            ("board_bottom", 1800),
            ("corner", 1600),
            ("wide", 800),
            ("back", 1600),
        ]
    ]


def test_a_side_no_region_uses_is_not_exported(tools: Tools, tmp_path: Path) -> None:
    only_top = shots.parse_regions({"corner": ("top", 0, 0, 10, 10)})
    shots.take_shots(tmp_path / "b.kicad_pcb", tmp_path / "o", only_top, render=False)
    (export,) = tools.of(tools.kicad)
    assert export[export.index("--layers") + 1].startswith("F.Cu")


def test_the_three_renders_use_the_finalize_command_lines(
    tools: Tools, tmp_path: Path
) -> None:
    pcb = tmp_path / "b.kicad_pcb"
    out = tmp_path / "shots"
    result = shots.take_shots(pcb, out, shots.board_regions(), render=True)
    renders = [c for c in tools.of(tools.kicad) if c[1] == "render"]
    # Copied from the three `kicad-cli pcb render` lines of the finalize script.
    assert renders == [
        [
            *["pcb", "render", "--side", "top", "--rotate", "-40,0,-20"],
            *["--width", "2400", "--height", "1600", "--quality", "high"],
            *["--zoom", "1.1", "-o", str(out / "render_iso.png"), str(pcb)],
        ],
        [
            *["pcb", "render", "--side", "top"],
            *["--width", "2400", "--height", "1500", "--quality", "high"],
            *["--zoom", "1.25", "-o", str(out / "render_top.png"), str(pcb)],
        ],
        [
            *["pcb", "render", "--side", "bottom"],
            *["--width", "2400", "--height", "1500", "--quality", "high"],
            *["--zoom", "1.25", "-o", str(out / "render_bottom.png"), str(pcb)],
        ],
    ]
    assert [p.name for p in result.renders] == [
        "render_iso.png",
        "render_top.png",
        "render_bottom.png",
    ]
    assert all(p in result.files for p in result.renders)


def test_render_views_can_prefix_the_file_names_and_pick_its_views(
    tools: Tools, tmp_path: Path
) -> None:
    """Let a fab export put the renders next to its PDFs under the board's name."""
    out = tmp_path / "docs"
    written = shots.render_views(
        tmp_path / "b.kicad_pcb",
        out,
        prefix="My_Board_revA_",
        views=shots.RENDER_VIEWS[1:],
    )
    assert [p.name for p in written] == [
        "My_Board_revA_render_top.png",
        "My_Board_revA_render_bottom.png",
    ]
    assert all(p.read_bytes() == PNG for p in written)


# --- what goes wrong ---------------------------------------------------------------


def test_a_missing_rsvg_convert_is_a_clear_message_and_nothing_is_exported(
    monkeypatch: pytest.MonkeyPatch, machine: FakeMachine, tmp_path: Path
) -> None:
    tools = install_tools(monkeypatch, machine, rsvg=False)
    with pytest.raises(ShotsError) as caught:
        shots.take_shots(tmp_path / "b.kicad_pcb", tmp_path / "o", regions_for_test())
    assert "rsvg-convert not found" in caught.value.message
    assert "brew install librsvg" in caught.value.message
    assert tools.calls == []
    assert not (tmp_path / "o").exists()


def test_rsvg_convert_failing_is_reported_with_its_own_words(
    monkeypatch: pytest.MonkeyPatch, machine: FakeMachine, tmp_path: Path
) -> None:
    install_tools(
        monkeypatch, machine, rsvg_run=lambda args: Run(1, "rsvg-convert: bad svg\n")
    )
    with pytest.raises(ShotsError) as caught:
        shots.take_shots(tmp_path / "b.kicad_pcb", tmp_path / "o", regions_for_test())
    assert "rsvg-convert failed on" in caught.value.message
    assert "(exit 1)" in caught.value.message
    assert "rsvg-convert: bad svg" in caught.value.message


def test_rsvg_convert_timing_out_is_reported(
    monkeypatch: pytest.MonkeyPatch, machine: FakeMachine, tmp_path: Path
) -> None:
    install_tools(
        monkeypatch,
        machine,
        rsvg_run=lambda args: Run(-1, error="timed out after 120 s"),
    )
    with pytest.raises(ShotsError, match="timed out after 120 s"):
        shots.take_shots(tmp_path / "b.kicad_pcb", tmp_path / "o", regions_for_test())


def test_an_old_png_is_not_mistaken_for_a_new_one(
    monkeypatch: pytest.MonkeyPatch, machine: FakeMachine, tmp_path: Path
) -> None:
    """Fail when rsvg-convert exits 0 and writes nothing, over an old PNG or not."""
    install_tools(monkeypatch, machine, rsvg_run=lambda args: Run(0, ""))
    out = tmp_path / "o"
    out.mkdir()
    (out / "corner.png").write_bytes(b"stale")
    only = shots.parse_regions({"corner": ("top", 0, 0, 10, 10)})
    with pytest.raises(ShotsError, match="wrote nothing at .*corner.png"):
        shots.take_shots(tmp_path / "b.kicad_pcb", out, only, render=False)
    assert not (out / "corner.png").exists()


def test_a_crop_of_something_that_is_not_an_svg_fails_before_converting(
    monkeypatch: pytest.MonkeyPatch, machine: FakeMachine, tmp_path: Path
) -> None:
    tools = install_tools(monkeypatch, machine)
    real_run = env._run

    def odd_svg(args: list[str], timeout: float = 20.0) -> Run:
        done = real_run(args, timeout)
        if args[1:4] == ["pcb", "export", "svg"]:
            Path(args[args.index("-o") + 1]).write_text("<html/>", encoding="utf-8")
        return done

    monkeypatch.setattr(env, "_run", odd_svg)
    only = shots.parse_regions({"corner": ("top", 0, 0, 10, 10)})
    with pytest.raises(ShotsError, match="cannot crop"):
        shots.take_shots(tmp_path / "b.kicad_pcb", tmp_path / "o", only, render=False)
    assert tools.rsvg is not None and tools.of(tools.rsvg) == []


def test_format_result_lists_the_regions_and_renders_with_a_project_relative_folder(
    tmp_path: Path,
) -> None:
    result = shots.ShotsResult(
        tmp_path / "out" / "shots",
        ("board_top", "corner"),
        (tmp_path / "out" / "shots" / "render_iso.png",),
        (),
    )
    assert shots.format_result(result, tmp_path).splitlines() == [
        "2 region shots in out/shots:",
        "  board_top.png",
        "  corner.png",
        "1 3D renders:",
        "  render_iso.png",
    ]
    skipped = shots.ShotsResult(Path("/elsewhere"), ("a",), (), ())
    assert shots.format_result(skipped, tmp_path).splitlines() == [
        "1 region shots in /elsewhere:",
        "  a.png",
        "3D renders skipped.",
    ]


# --- the command ------------------------------------------------------------------


@pytest.fixture
def project(
    project_root: Path, fake_pcbnew: Any, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Make a project with a board, two declared regions, and make it current."""
    write_file(
        project_root / "layout.py",
        """
        SHOTS = {
            "corner": ("top", 0, 0, 10, 10),
            "back": ("bottom", 0, 0, 10, 10),
        }
        """,
    )
    write_file(project_root / "kicad" / "my_board.kicad_pcb", "(kicad_pcb)")
    monkeypatch.chdir(project_root)
    return project_root


def invoke(*args: str) -> Result:
    """Run `pcbkit shots ...` in-process."""
    return CliRunner().invoke(cli, ["shots", *args])


def test_shots_needs_pcbnew_for_now_and_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the plan's tier: shots needs only kicad-cli, but it is a tier 2 command."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    result = invoke()
    assert result.exit_code == 1
    assert "pcbnew isn't importable here. In the board project, run: pcbkit setup" in (
        result.output
    )


def test_shots_in_a_project_makes_every_region_and_render_in_out_shots(
    project: Path, tools: Tools
) -> None:
    result = invoke()
    assert result.exit_code == 0, result.output
    out = project / "out" / "shots"
    assert result.output.splitlines() == [
        "4 region shots in out/shots:",
        "  board_top.png",
        "  board_bottom.png",
        "  corner.png",
        "  back.png",
        "3 3D renders:",
        "  render_iso.png",
        "  render_top.png",
        "  render_bottom.png",
    ]
    for name in ("board_top", "board_bottom", "corner", "back"):
        assert (out / f"{name}.svg").is_file() and (out / f"{name}.png").is_file()
    board = str(project / "kicad" / "my_board.kicad_pcb")
    assert all(c[-1] == board for c in tools.of(tools.kicad))


def test_no_render_skips_the_renders(project: Path, tools: Tools) -> None:
    result = invoke("--no-render")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "3D renders skipped."
    assert not [c for c in tools.of(tools.kicad) if c[1] == "render"]
    assert not list((project / "out" / "shots").glob("render_*"))


def test_region_picks_regions_and_a_stranger_lists_the_real_ones(
    project: Path, tools: Tools
) -> None:
    result = invoke("--region", "back", "--region", "corner", "--no-render")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:3] == [
        "2 region shots in out/shots:",
        "  corner.png",
        "  back.png",
    ]
    assert not (project / "out" / "shots" / "board_top.png").exists()
    bad = invoke("--region", "nope")
    assert bad.exit_code == 1
    assert bad.output.strip() == (
        "Error: no region called 'nope'; the regions are: "
        "board_top, board_bottom, corner, back"
    )


def test_out_and_pcb_say_where_to_write_and_what_to_shoot(
    project: Path, tools: Tools, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere.kicad_pcb"
    other.write_text("(kicad_pcb)", encoding="utf-8")
    target = tmp_path / "pics"
    result = invoke("--out", str(target), "--pcb", str(other), "--no-render")
    assert result.exit_code == 0, result.output
    assert (target / "corner.png").is_file()
    assert not (project / "out").exists()
    assert all(c[-1] == str(other) for c in tools.of(tools.kicad))
    assert result.output.splitlines()[0] == f"4 region shots in {target}:"


def test_a_project_with_no_board_yet_is_told_what_to_run(
    project: Path, tools: Tools
) -> None:
    (project / "kicad" / "my_board.kicad_pcb").unlink()
    result = invoke()
    assert result.exit_code == 1
    assert "no board at" in result.output and "my_board.kicad_pcb" in result.output
    assert "pcbkit finalize" in result.output and "--pcb" in result.output
    assert tools.calls == []


def test_a_bad_shots_in_layout_py_stops_the_command_before_any_export(
    project: Path, tools: Tools
) -> None:
    write_file(project / "layout.py", 'SHOTS = {"x": ("top", 5, 5, 1, 1)}\n')
    result = invoke()
    assert result.exit_code == 1
    assert "layout.py: 1 problem in SHOTS" in result.output
    assert tools.calls == []


def test_shots_outside_a_project_says_so(
    fake_pcbnew: Any, tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = invoke()
    assert result.exit_code == 1
    assert "no pcbkit.toml" in result.output
    assert tools.calls == []


def test_shots_help_lists_every_option() -> None:
    output = invoke("--help").output
    for option in ("--out", "--pcb", "--region", "--no-render"):
        assert option in output
    assert "SHOTS in layout.py" in " ".join(output.split())
