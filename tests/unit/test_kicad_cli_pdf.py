"""Unit tests for the two PDF wrappers in pcbkit.kicad.cli, against a faked kicad-cli.

Same approach as test_kicad_cli.py: the fake records the command line and writes the
file the real tool would, and everything the wrappers do with that is the real code.
The expected command lines are the ones the fab export has always run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbkit.kicad import cli
from tests.fake_machine import FakeMachine
from tests.unit.test_kicad_cli import fake_run, writes_text


@pytest.fixture
def kicad(machine: FakeMachine) -> str:
    """Install a fake kicad-cli on the fake PATH and return its path."""
    return str(
        machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
    )


def test_the_schematic_pdf_command_line(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("%PDF-1.7"))
    run = cli.export_sch_pdf(tmp_path / "b.kicad_sch", tmp_path / "b_schematic.pdf")
    # the fab export: kicad-cli sch export pdf -o out.pdf X.kicad_sch
    assert calls[0].args == [
        kicad,
        "sch",
        "export",
        "pdf",
        "-o",
        str(tmp_path / "b_schematic.pdf"),
        str(tmp_path / "b.kicad_sch"),
    ]
    assert calls[0].timeout == cli.TIMEOUT_EXPORT
    assert run.files == (str(tmp_path / "b_schematic.pdf"),)


def test_the_board_pdf_puts_the_layers_on_one_page_by_default(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("%PDF-1.7"))
    run = cli.export_pcb_pdf(
        tmp_path / "b.kicad_pcb",
        tmp_path / "b_top_copper.pdf",
        ["F.Cu", "F.SilkS", "Edge.Cuts"],
    )
    # the fab export: kicad-cli pcb export pdf --layers F.Cu,F.SilkS,Edge.Cuts
    #   --mode-single -o out.pdf PCB
    assert calls[0].args == [
        kicad,
        "pcb",
        "export",
        "pdf",
        "--layers",
        "F.Cu,F.SilkS,Edge.Cuts",
        "--mode-single",
        "-o",
        str(tmp_path / "b_top_copper.pdf"),
        str(tmp_path / "b.kicad_pcb"),
    ]
    assert run.files == (str(tmp_path / "b_top_copper.pdf"),)


def test_the_board_pdf_can_give_each_layer_its_own_page(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path
) -> None:
    calls = fake_run(monkeypatch, kicad, write=writes_text("%PDF-1.7"))
    cli.export_pcb_pdf(
        tmp_path / "b.kicad_pcb",
        tmp_path / "b.pdf",
        ["B.Cu", "Edge.Cuts"],
        mode_single=False,
    )
    assert "--mode-single" not in calls[0].args
    assert calls[0].args[4:6] == ["--layers", "B.Cu,Edge.Cuts"]


@pytest.mark.parametrize("export", ["sch", "pcb"])
def test_a_pdf_export_that_writes_nothing_is_an_error(
    monkeypatch: pytest.MonkeyPatch, kicad: str, tmp_path: Path, export: str
) -> None:
    fake_run(monkeypatch, kicad)  # exits 0 and writes no file
    with pytest.raises(cli.KicadCliError, match="wrote nothing"):
        if export == "sch":
            cli.export_sch_pdf(tmp_path / "b.kicad_sch", tmp_path / "x.pdf")
        else:
            cli.export_pcb_pdf(tmp_path / "b.kicad_pcb", tmp_path / "x.pdf", ["F.Cu"])
