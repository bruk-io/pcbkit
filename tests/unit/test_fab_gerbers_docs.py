"""Unit tests for pcbkit.fab.gerbers (the zip) and pcbkit.fab.docs (the commands).

kicad-cli and rsvg-convert are faked: the fake records each command line and writes the
file the real tool would, and everything pcbkit does around that is the real code. The
expected command lines are the ones the fab export and finalize.sh have always run. The
real tools are exercised in tests/integration/test_fab_real_kicad.py.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import click
import pytest

from pcbkit.fab import docs, gerbers
from pcbkit.kicad import env
from pcbkit.kicad.env import Run
from tests.fake_machine import FakeMachine

# --- the zip -------------------------------------------------------------------------


def test_the_zip_holds_every_file_in_the_folder_sorted_and_flat(tmp_path: Path) -> None:
    folder = tmp_path / "g"
    folder.mkdir()
    for name in ("b-Edge_Cuts.gbr", "a-F_Cu.gbr", "c-job.gbrjob"):
        (folder / name).write_text(name, encoding="utf-8")
    (folder / "subfolder").mkdir()  # not a file: left out
    names = gerbers.make_zip(folder, tmp_path / "out.zip")
    assert names == ["a-F_Cu.gbr", "b-Edge_Cuts.gbr", "c-job.gbrjob"]
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert archive.namelist() == names
        assert archive.read("a-F_Cu.gbr") == b"a-F_Cu.gbr"
        assert archive.getinfo("a-F_Cu.gbr").compress_type == zipfile.ZIP_DEFLATED


# --- the fake tools ------------------------------------------------------------------


class Tools:
    """A fake kicad-cli and rsvg-convert that log commands and write their outputs."""

    def __init__(self, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.fail_rsvg = False
        self.kicad = str(
            machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
        )
        self.rsvg = str(
            machine.exe(
                machine.usr_bin / "rsvg-convert",
                "rsvg-convert version 2.60.0",
                on_path="rsvg-convert",
            )
        )
        monkeypatch.setattr(env, "_run", self.run)

    def run(self, args: list[str], timeout: float = 20.0) -> Run:
        """Answer a version probe, or log the command and write the file it makes."""
        if args[1:] == ["--version"]:
            return Run(0, "10.0.6\n" if args[0] == self.kicad else "version 2.60.0\n")
        self.calls.append(list(args))
        if args[0] == self.rsvg and self.fail_rsvg:
            return Run(1, "Error reading SVG\n")
        out = Path(args[args.index("-o") + 1])
        out.write_text("<svg/>" if out.suffix == ".svg" else "output", encoding="utf-8")
        return Run(0, "")

    def commands(self) -> list[list[str]]:
        """Return the logged commands with the tool's path cut down to its name."""
        return [[Path(c[0]).name, *c[1:]] for c in self.calls]


@pytest.fixture
def tools(machine: FakeMachine, monkeypatch: pytest.MonkeyPatch) -> Tools:
    """Install the fake tools."""
    return Tools(machine, monkeypatch)


def tail(args: list[str]) -> list[str]:
    """Return the options of a command, without the paths."""
    return [a for a in args if not a.startswith("/")]


# --- the documents -------------------------------------------------------------------


def test_the_document_names_start_with_the_fab_name() -> None:
    names = docs.doc_names("Board_revA")
    assert names.schematic == "Board_revA_schematic.pdf"
    assert names.assembly_pdf == "Board_revA_assembly_top.pdf"
    assert names.assembly_png == "Board_revA_assembly_top.png"
    assert names.top_copper == "Board_revA_top_copper.pdf"
    assert names.bottom_copper == "Board_revA_bottom_copper.pdf"
    assert names.render_iso == "Board_revA_render_iso.png"
    assert names.render_top == "Board_revA_render_top.png"
    assert names.render_bottom == "Board_revA_render_bottom.png"


def test_the_documents_are_written_with_the_commands_the_fab_export_ran(
    tools: Tools, tmp_path: Path
) -> None:
    sch, pcb, folder = tmp_path / "b.kicad_sch", tmp_path / "b.kicad_pcb", tmp_path
    written = docs.export_documents(sch, pcb, folder, "B_revA", render=False)
    commands = tools.commands()
    assert [c[0] for c in commands] == [
        "kicad-cli",  # schematic pdf
        "kicad-cli",  # assembly svg
        "rsvg-convert",  # pdf
        "rsvg-convert",  # png
        "kicad-cli",  # top copper
        "kicad-cli",  # bottom copper
    ]
    assert tail(commands[0]) == ["kicad-cli", "sch", "export", "pdf", "-o"]
    svg = commands[1][commands[1].index("-o") + 1]
    assert commands[1][1:] == [
        "pcb",
        "export",
        "svg",
        "--layers",
        "F.Fab,Edge.Cuts",
        "--mode-single",
        "--exclude-drawing-sheet",
        "--fit-page-to-board",
        "--black-and-white",
        "-o",
        svg,
        str(pcb),
    ]
    assert commands[2][1:] == [
        "-f",
        "pdf",
        "-z",
        "2.6",
        "-b",
        "white",
        "-o",
        str(folder / "B_revA_assembly_top.pdf"),
        svg,
    ]
    assert commands[3][1:] == [
        "-z",
        "9",
        "-b",
        "white",
        "-o",
        str(folder / "B_revA_assembly_top.png"),
        svg,
    ]
    assert commands[4][1:6] == [
        "pcb",
        "export",
        "pdf",
        "--layers",
        "F.Cu,F.SilkS,Edge.Cuts",
    ]
    assert commands[4][6] == "--mode-single"
    assert commands[5][1:6] == ["pcb", "export", "pdf", "--layers", "B.Cu,Edge.Cuts"]
    assert [p.name for p in written] == [
        "B_revA_schematic.pdf",
        "B_revA_assembly_top.pdf",
        "B_revA_assembly_top.png",
        "B_revA_top_copper.pdf",
        "B_revA_bottom_copper.pdf",
    ]
    assert not Path(svg).exists()  # the scratch SVG is gone


def test_the_three_renders_use_the_views_finalize_has_always_made(
    tools: Tools, tmp_path: Path
) -> None:
    pcb = tmp_path / "b.kicad_pcb"
    names = docs.doc_names("B_revA")
    written = docs.renders(pcb, names, tmp_path)
    renders = [c[1:] for c in tools.commands()]
    # finalize.sh: kicad-cli pcb render --side top --rotate "-40,0,-20" --width 2400
    #   --height 1600 --quality high --zoom 1.1 -o iso.png PCB
    assert renders[0] == [
        "pcb",
        "render",
        "--side",
        "top",
        "--rotate",
        "-40,0,-20",
        "--width",
        "2400",
        "--height",
        "1600",
        "--quality",
        "high",
        "--zoom",
        "1.1",
        "-o",
        str(tmp_path / "B_revA_render_iso.png"),
        str(pcb),
    ]
    assert renders[1] == [
        "pcb",
        "render",
        "--side",
        "top",
        "--width",
        "2400",
        "--height",
        "1500",
        "--quality",
        "high",
        "--zoom",
        "1.25",
        "-o",
        str(tmp_path / "B_revA_render_top.png"),
        str(pcb),
    ]
    assert renders[2][2:4] == ["--side", "bottom"]
    assert renders[2][-2] == str(tmp_path / "B_revA_render_bottom.png")
    assert [p.name for p in written] == [
        "B_revA_render_iso.png",
        "B_revA_render_top.png",
        "B_revA_render_bottom.png",
    ]


def render_commands(tools: Tools) -> list[list[str]]:
    """Return the logged 3D render commands."""
    return [c for c in tools.commands() if c[1:3] == ["pcb", "render"]]


def test_renders_are_made_only_when_asked_for(tools: Tools, tmp_path: Path) -> None:
    args = (tmp_path / "b.kicad_sch", tmp_path / "b.kicad_pcb", tmp_path, "B_revA")
    without = docs.export_documents(*args, render=False)
    assert render_commands(tools) == []
    with_renders = docs.export_documents(*args, render=True)
    assert len(with_renders) == len(without) + 3
    assert len(render_commands(tools)) == 3


def test_a_missing_rsvg_convert_says_how_to_get_it(
    tools: Tools, machine: FakeMachine, tmp_path: Path
) -> None:
    del machine.path_tools["rsvg-convert"]
    with pytest.raises(click.ClickException, match="brew install librsvg"):
        docs.assembly_drawing(
            tmp_path / "b.kicad_pcb", tmp_path / "a.pdf", tmp_path / "a.png"
        )


def test_a_failing_rsvg_convert_shows_its_output(tools: Tools, tmp_path: Path) -> None:
    tools.fail_rsvg = True
    with pytest.raises(
        click.ClickException, match=r"(?s)rsvg-convert failed.*Error reading SVG"
    ):
        docs.assembly_drawing(
            tmp_path / "b.kicad_pcb", tmp_path / "a.pdf", tmp_path / "a.png"
        )
