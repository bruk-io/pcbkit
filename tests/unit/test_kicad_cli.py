"""Unit tests for the kicad-cli wrappers, against a kicad-cli that is faked.

The fake stands in for the tool and nothing else: it records the command line and the
time limit it was given, and writes the report or export files that the real tool
would. Everything the wrappers do with that (the command line they build, what they
return, when they raise, how they parse the report) is the real code. Each expected
command line is copied from the board scripts the wrappers replace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click
import pytest

from pcbkit.kicad import cli, env
from pcbkit.kicad.env import Run
from tests.fake_machine import FakeMachine

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reports"

Writer = Callable[[list[str]], None]


@dataclass
class Call:
    """One command the fake kicad-cli was asked to run."""

    args: list[str]
    timeout: float


def target_of(args: list[str]) -> Path:
    """Return the path after ``-o``: where the real tool would write."""
    return Path(args[args.index("-o") + 1])


def writes_text(text: str) -> Writer:
    """Return a writer that puts ``text`` in the file named by ``-o``."""

    def write(args: list[str]) -> None:
        target_of(args).write_text(text, encoding="utf-8")

    return write


def writes_report(name: str) -> Writer:
    """Return a writer that puts a fixture report in the file named by ``-o``."""
    return writes_text((FIXTURES / name).read_text(encoding="utf-8"))


def writes_files(*names: str) -> Writer:
    """Return a writer that creates ``names`` in the folder named by ``-o``."""

    def write(args: list[str]) -> None:
        folder = target_of(args)
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_text("x", encoding="utf-8")

    return write


@pytest.fixture
def kicad(machine: FakeMachine) -> str:
    """Install a fake kicad-cli on the fake PATH and return its path."""
    return str(
        machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
    )


def fake_run(
    monkeypatch: pytest.MonkeyPatch,
    kicad: str,
    *,
    write: Writer | None = None,
    returncode: int = 0,
    output: str = "",
    error: str = "",
) -> list[Call]:
    """Make ``kicad`` answer every run; return the list the runs are recorded in."""
    calls: list[Call] = []

    def run(args: list[str], timeout: float = 20.0) -> Run:
        if args == [kicad, "--version"]:  # the version probe in env.find_kicad_cli
            return Run(0, "10.0.6\n")
        calls.append(Call(list(args), timeout))
        if write is not None and returncode == 0 and not error:
            write(args)
        return Run(returncode, output, error)

    monkeypatch.setattr(env, "_run", run)
    return calls


# --- the command lines ---------------------------------------------------------------


def test_erc_runs_the_build_scripts_command_line(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_report("erc_clean.rpt"))
    result = cli.erc(tmp_path / "b.kicad_sch", tmp_path / "erc.rpt")
    # build.sh: kicad-cli sch erc -o erc.rpt X.kicad_sch
    assert [c.args for c in calls] == [
        [
            kicad,
            "sch",
            "erc",
            "-o",
            str(tmp_path / "erc.rpt"),
            str(tmp_path / "b.kicad_sch"),
        ]
    ]
    assert calls[0].timeout == cli.TIMEOUT_ERC
    assert result.report.summary == "ERC messages: 0  Errors 0  Warnings 0"
    assert result.report_path == str(tmp_path / "erc.rpt")
    assert result.run.files == (str(tmp_path / "erc.rpt"),)
    assert result.run.args == tuple(calls[0].args)


def test_erc_can_ask_for_every_severity(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_report("erc_errors.rpt"))
    result = cli.erc(tmp_path / "b.kicad_sch", tmp_path / "erc.rpt", severity_all=True)
    # the validation check: kicad-cli sch erc --severity-all -o rpt SCH
    assert calls[0].args[:4] == [kicad, "sch", "erc", "--severity-all"]
    assert calls[0].args[4] == "-o"
    assert result.report.errors == 4


def test_the_netlist_export_uses_the_kicad_s_expression_format(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("(export)"))
    run = cli.export_netlist(tmp_path / "b.kicad_sch", tmp_path / "b.net")
    # build.sh: kicad-cli sch export netlist --format kicadsexpr -o X.net X.kicad_sch
    assert calls[0].args == [
        kicad,
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadsexpr",
        "-o",
        str(tmp_path / "b.net"),
        str(tmp_path / "b.kicad_sch"),
    ]
    assert run.files == (str(tmp_path / "b.net"),)


@pytest.mark.parametrize(
    ("options", "flags"),
    [
        ({}, []),
        ({"schematic_parity": True}, ["--schematic-parity"]),  # route.sh, finalize.sh
        (
            {"severity_all": True, "schematic_parity": True},
            ["--severity-all", "--schematic-parity"],  # the validation check
        ),
        ({"severity_all": True}, ["--severity-all"]),
    ],
)
def test_drc_adds_its_two_flags_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
    kicad: str,
    tmp_path: Path,
    options: dict[str, bool],
    flags: list[str],
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_report("drc_clean.rpt"))
    result = cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt", **options)
    assert calls[0].args == [
        kicad,
        "pcb",
        "drc",
        *flags,
        "-o",
        str(tmp_path / "drc.rpt"),
        str(tmp_path / "b.kicad_pcb"),
    ]
    assert calls[0].timeout == cli.TIMEOUT_DRC
    assert result.report.counts == {
        "DRC violations": 0,
        "unconnected pads": 0,
        "Footprint errors": 0,
    }
    assert "--exit-code-violations" not in calls[0].args


def test_gerbers_go_to_a_folder_with_a_trailing_slash_and_return_the_job_file(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    names = ["b-F_Cu.gbr", "b-job.gbrjob", "b-Edge_Cuts.gbr"]
    calls = fake_run(monkeypatch, kicad, write=writes_files(*names))
    gdir = tmp_path / "gerbers"
    run = cli.export_gerbers(tmp_path / "b.kicad_pcb", gdir)
    # the fab export: kicad-cli pcb export gerbers --layers ... --no-protel-ext
    #   --use-drill-file-origin -o gdir/ PCB
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "gerbers",
        "--layers",
        "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts",
        "--no-protel-ext",
        "--use-drill-file-origin",
        "-o",
        str(gdir) + "/",
        str(tmp_path / "b.kicad_pcb"),
    ]
    assert run.files == tuple(str(gdir / n) for n in sorted(names))
    assert [f for f in run.files if f.endswith(".gbrjob")] == [
        str(gdir / "b-job.gbrjob")
    ]


def test_gerber_layers_and_the_two_switches_can_be_changed(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_files("a.gbr"))
    cli.export_gerbers(
        tmp_path / "b.kicad_pcb",
        tmp_path / "g",
        layers=["F.Cu", "Edge.Cuts"],
        protel_ext=True,
        use_drill_origin=False,
    )
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "gerbers",
        "--layers",
        "F.Cu,Edge.Cuts",
        "-o",
        str(tmp_path / "g") + "/",
        str(tmp_path / "b.kicad_pcb"),
    ]


def test_drill_files_use_the_plot_origin_and_separate_plated_holes(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_files("b-PTH.drl", "b-NPTH.drl"))
    run = cli.export_drill(tmp_path / "b.kicad_pcb", tmp_path / "g")
    # the fab export: kicad-cli pcb export drill --format excellon
    #   --excellon-separate-th --drill-origin plot --generate-map --map-format
    #   gerberx2 --excellon-units mm -o gdir/ PCB
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "drill",
        "--format",
        "excellon",
        "--excellon-separate-th",
        "--drill-origin",
        "plot",
        "--generate-map",
        "--map-format",
        "gerberx2",
        "--excellon-units",
        "mm",
        "-o",
        str(tmp_path / "g") + "/",
        str(tmp_path / "b.kicad_pcb"),
    ]
    assert len(run.files) == 2


def test_positions_are_a_front_side_csv_in_millimetres_from_the_drill_origin(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("Ref,Val\n"))
    cli.export_positions(tmp_path / "b.kicad_pcb", tmp_path / "pos.csv")
    # the fab export: kicad-cli pcb export pos --side front --format csv --units mm
    #   --use-drill-file-origin -o raw PCB
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "pos",
        "--side",
        "front",
        "--format",
        "csv",
        "--units",
        "mm",
        "--use-drill-file-origin",
        "-o",
        str(tmp_path / "pos.csv"),
        str(tmp_path / "b.kicad_pcb"),
    ]


def test_svg_is_one_file_fitted_to_the_board_without_the_drawing_sheet(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("<svg/>"))
    layers = ["F.Cu", "F.SilkS", "F.CrtYd", "Edge.Cuts", "F.Fab"]
    cli.export_svg(tmp_path / "b.kicad_pcb", tmp_path / "pcb.svg", layers)
    # render.sh: kicad-cli pcb export svg --layers "$L" --mode-single
    #   --exclude-drawing-sheet --fit-page-to-board -o out.svg PCB
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "svg",
        "--layers",
        "F.Cu,F.SilkS,F.CrtYd,Edge.Cuts,F.Fab",
        "--mode-single",
        "--exclude-drawing-sheet",
        "--fit-page-to-board",
        "-o",
        str(tmp_path / "pcb.svg"),
        str(tmp_path / "b.kicad_pcb"),
    ]


def test_svg_can_be_black_and_white_for_the_assembly_drawing(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("<svg/>"))
    cli.export_svg(
        tmp_path / "b.kicad_pcb",
        tmp_path / "asm.svg",
        ["F.Fab", "Edge.Cuts"],
        black_and_white=True,
    )
    # the fab export: ... --fit-page-to-board --black-and-white -o svg PCB
    assert calls[0].args == [
        kicad,
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
        str(tmp_path / "asm.svg"),
        str(tmp_path / "b.kicad_pcb"),
    ]


def test_svg_switches_can_be_turned_off(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("<svg/>"))
    cli.export_svg(
        tmp_path / "b.kicad_pcb",
        tmp_path / "x.svg",
        ["F.Cu"],
        mode_single=False,
        exclude_drawing_sheet=False,
        fit_page_to_board=False,
    )
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "svg",
        "--layers",
        "F.Cu",
        "-o",
        str(tmp_path / "x.svg"),
        str(tmp_path / "b.kicad_pcb"),
    ]


@pytest.mark.parametrize(
    ("options", "middle"),
    [
        # finalize.sh, the isometric view
        (
            {
                "side": "top",
                "rotate": (-40, 0, -20),
                "width": 2400,
                "height": 1600,
                "quality": "high",
                "zoom": 1.1,
            },
            "--side top --rotate -40,0,-20 --width 2400 --height 1600 "
            "--quality high --zoom 1.1",
        ),
        # finalize.sh, the top view
        (
            {
                "side": "top",
                "width": 2400,
                "height": 1500,
                "quality": "high",
                "zoom": 1.25,
            },
            "--side top --width 2400 --height 1500 --quality high --zoom 1.25",
        ),
        # finalize.sh, the bottom view
        (
            {
                "side": "bottom",
                "width": 2400,
                "height": 1500,
                "quality": "high",
                "zoom": 1.25,
            },
            "--side bottom --width 2400 --height 1500 --quality high --zoom 1.25",
        ),
        ({}, "--side top"),
    ],
)
def test_the_render_options_come_in_the_order_the_finalize_script_gave_them(
    monkeypatch: pytest.MonkeyPatch,
    kicad: str,
    tmp_path: Path,
    options: dict[str, object],
    middle: str,
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("png"))
    cli.render_3d(tmp_path / "b.kicad_pcb", tmp_path / "r.png", **options)  # type: ignore[arg-type]
    assert calls[0].args == [
        kicad,
        "pcb",
        "render",
        *middle.split(),
        "-o",
        str(tmp_path / "r.png"),
        str(tmp_path / "b.kicad_pcb"),
    ]
    assert calls[0].timeout == cli.TIMEOUT_RENDER == 300.0


def test_every_wrapper_gives_the_tool_a_time_limit_longer_than_the_default(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    """The default 20 s of env._run would kill a DRC or a render part-way."""
    calls = fake_run(monkeypatch, kicad, write=writes_text("x"))
    pcb, sch = tmp_path / "b.kicad_pcb", tmp_path / "b.kicad_sch"
    calls.clear()
    cli.export_positions(pcb, tmp_path / "p.csv")
    cli.export_svg(pcb, tmp_path / "p.svg", ["F.Cu"])
    cli.render_3d(pcb, tmp_path / "p.png")
    cli.export_netlist(sch, tmp_path / "p.net")
    assert [c.timeout for c in calls] == [300.0, 300.0, 300.0, 300.0]
    assert cli.TIMEOUT_DRC == 900.0


# --- failures ------------------------------------------------------------------


def test_a_non_zero_exit_raises_with_the_tools_own_words(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    fake_run(
        monkeypatch, kicad, returncode=1, output="Loading board\nFailed to load board\n"
    )
    with pytest.raises(cli.KicadCliError) as raised:
        cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt")
    assert "kicad-cli pcb drc failed (exit 1)" in raised.value.message
    assert "Failed to load board" in raised.value.message
    assert raised.value.command[:3] == (kicad, "pcb", "drc")
    assert "Loading board" in raised.value.output
    assert isinstance(raised.value, click.ClickException)


def test_a_run_that_could_not_start_or_timed_out_raises(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    fake_run(monkeypatch, kicad, returncode=-1, error="timed out after 300 s")
    with pytest.raises(
        cli.KicadCliError, match=r"render failed \(timed out after 300 s\)"
    ):
        cli.render_3d(tmp_path / "b.kicad_pcb", tmp_path / "r.png")


def test_a_run_that_exits_zero_but_writes_nothing_raises(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    fake_run(monkeypatch, kicad, output="Done.\n")  # no writer: nothing is created
    with pytest.raises(cli.KicadCliError, match="wrote nothing at"):
        cli.export_positions(tmp_path / "b.kicad_pcb", tmp_path / "pos.csv")


def test_an_export_into_a_folder_that_ends_up_empty_raises(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    (tmp_path / "g").mkdir()
    fake_run(monkeypatch, kicad, output="Done.\n")
    with pytest.raises(cli.KicadCliError, match="wrote nothing at"):
        cli.export_gerbers(tmp_path / "b.kicad_pcb", tmp_path / "g")


def test_violations_in_a_report_are_results_not_failures(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    fake_run(monkeypatch, kicad, write=writes_report("drc_clearance_and_holes.rpt"))
    result = cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt")
    assert not result.report.clean
    assert result.report.categories["clearance"] == 1


def test_a_report_in_an_unknown_format_raises_instead_of_reading_as_clean(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    fake_run(monkeypatch, kicad, write=writes_text("Something else entirely\n"))
    with pytest.raises(cli.ReportError):
        cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt")
    with pytest.raises(cli.ReportError):
        cli.erc(tmp_path / "b.kicad_sch", tmp_path / "erc.rpt")


def test_a_missing_kicad_cli_is_a_message_that_says_how_to_fix_it(
    machine: FakeMachine, tmp_path: Path
) -> None:
    with pytest.raises(click.ClickException, match="pcbkit doctor"):
        cli.drc(tmp_path / "b.kicad_pcb", tmp_path / "drc.rpt")
    assert machine.calls == []  # nothing was run: there was nothing to run
