"""Integration: the silkscreen pass on a tiny placed board, with real KiCad and pcbnew.

The board is the one in tests/tiny_project.py, placed by `place_board`: a header, two
resistors and an LED on a 40 x 24 mm board with two mounting holes. Each test copies it,
gives the copy a silk.py, runs ``apply_silk`` and reads the saved board back. It needs
KiCad's own Python (``import pcbnew``): see the head of
tests/integration/test_kicad_core.py for how to make .venv-kicad, then run

    .venv-kicad/bin/python -m pytest -m kicad -q

In any other Python these tests are skipped.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit import place, silk  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.project import ProjectError, load_project  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402
from tests.tiny_project import LAYOUT, make_project, run_stages  # noqa: E402

pytestmark = pytest.mark.kicad

# the tiny project's layout with the LED placed too, on the board rather than parked
ROOMY_LAYOUT = LAYOUT.replace('"HR1"', '"D1": (30.0, 17.0, 0),\n    "HR1"')

SILK = """\
LABELS = [
    ("TINY", 20.0, 20.0, 1.0, 0),
    ("V1", 33.0, 6.0, 0.8, 90),
]
CONN_LABELS = {"J1": "SUPPLY 3V3/G"}
HIDE_REF = {"H1", "H2"}
COMPANY = "Acme"
COMMENTS = ["2 layer, 1.6 mm FR4"]
DATE = "2026-01-02"
"""


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep project modules and sys.path entries from outliving a test."""
    with restored_imports():
        yield


@pytest.fixture(scope="module")
def placed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Place the tiny project once; return its folder. The tests only copy it."""
    root = make_project(
        tmp_path_factory.mktemp("silk") / "tiny_board", layout=ROOMY_LAYOUT
    )
    with restored_imports():
        run_stages(root)
    return root


@pytest.fixture
def project(placed: Path, tmp_path: Path) -> Path:
    """Return a private copy of the placed project, with a silk.py."""
    root = tmp_path / "tiny_board"
    shutil.copytree(placed, root)
    (root / "silk.py").write_text(SILK, encoding="utf-8")
    return root


def pcb_of(root: Path) -> Path:
    """Return the project's board file."""
    return root / "kicad" / "tiny_board.kicad_pcb"


def load(path: Path) -> Any:
    """Load a board file with pcbnew."""
    return pcbnew.LoadBoard(str(path))


def texts_of(board: Any) -> dict[str, list[Any]]:
    """Return the board's own texts by their content."""
    found: dict[str, list[Any]] = {}
    for item in board.GetDrawings():
        if item.GetClass() == "PCB_TEXT":
            found.setdefault(item.GetText(), []).append(item)
    return found


def layer_of(item: Any) -> str:
    """Return the name of a board item's layer."""
    return item.GetLayerName()


# --- what the pass adds ------------------------------------------------------------


def test_the_fixed_labels_are_added_at_their_spots(project: Path) -> None:
    result = silk.apply_silk(load_project(project))
    texts = texts_of(load(result.pcb))
    (tiny,) = texts["TINY"]
    assert kb.to_local(tiny.GetPosition()) == pytest.approx((20.0, 20.0), abs=1e-6)
    assert layer_of(tiny) == "F.Silkscreen"
    # 1 mm text is bold: 0.2 of its size; 0.8 mm text is 0.15, the least a fab prints
    assert pcbnew.ToMM(tiny.GetTextThickness()) == pytest.approx(0.2)
    (v1,) = texts["V1"]
    assert v1.GetTextAngleDegrees() == pytest.approx(90.0)
    assert pcbnew.ToMM(v1.GetTextThickness()) == pytest.approx(0.15)
    assert pcbnew.ToMM(v1.GetTextWidth()) == pytest.approx(0.8)


def test_a_connector_label_goes_beside_its_part(project: Path) -> None:
    board = load(silk.apply_silk(load_project(project)).pcb)
    (label,) = texts_of(board)["SUPPLY 3V3/G"]
    x, y = kb.to_local(label.GetPosition())
    j1 = board.FindFootprintByReference("J1")
    jx, jy = kb.to_local(j1.GetPosition())
    # the first choice is just below the courtyard, centred on it: the board is roomy
    assert abs(x - jx) < 3.0
    assert 0.5 < y - jy < 6.5
    assert pcbnew.ToMM(label.GetTextWidth()) == pytest.approx(0.8)


def test_the_count_is_the_texts_added_to_the_board(project: Path) -> None:
    result = silk.apply_silk(load_project(project))
    assert result.texts == 3  # TINY, V1 and the connector label
    assert sum(len(v) for v in texts_of(load(result.pcb)).values()) == 3
    assert result.warnings == []
    assert result.moved_to_fab == []


def test_references_sit_on_the_silkscreen_unless_hidden(project: Path) -> None:
    board = load(silk.apply_silk(load_project(project)).pcb)
    for ref in ("J1", "R1", "HR1", "D1"):
        text = board.FindFootprintByReference(ref).Reference()
        assert layer_of(text) == "F.Silkscreen", ref
        assert pcbnew.ToMM(text.GetTextHeight()) == pytest.approx(0.8)
        assert pcbnew.ToMM(text.GetTextThickness()) == pytest.approx(0.15)
    for ref in ("H1", "H2"):  # HIDE_REF
        assert layer_of(board.FindFootprintByReference(ref).Reference()) == "F.Fab"


def test_values_never_print_on_the_silkscreen(project: Path) -> None:
    board = load(silk.apply_silk(load_project(project)).pcb)
    for footprint in board.GetFootprints():
        value = footprint.Value()
        assert layer_of(value) == "F.Fab", footprint.GetReference()
        assert not value.IsVisible(), footprint.GetReference()


def test_the_title_block_takes_the_board_and_silk_py(project: Path) -> None:
    block = load(silk.apply_silk(load_project(project)).pcb).GetTitleBlock()
    assert (block.GetTitle(), block.GetRevision()) == ("Tiny Board", "A")
    assert (block.GetCompany(), block.GetDate()) == ("Acme", "2026-01-02")
    assert block.GetComment(0) == "2 layer, 1.6 mm FR4"
    assert block.GetComment(1) == ""


def test_the_date_is_left_empty_not_today_when_silk_py_gives_none(
    project: Path,
) -> None:
    """A run on another day must not change the board."""
    (project / "silk.py").write_text("LABELS = []\n", encoding="utf-8")
    block = load(silk.apply_silk(load_project(project)).pcb).GetTitleBlock()
    assert block.GetDate() == ""
    assert block.GetCompany() == ""
    assert block.GetTitle() == "Tiny Board"


def test_a_project_without_a_silk_py_still_gets_references_and_a_title(
    project: Path,
) -> None:
    (project / "silk.py").unlink()
    result = silk.apply_silk(load_project(project))
    board = load(result.pcb)
    assert result.texts == 0
    assert layer_of(board.FindFootprintByReference("R1").Reference()) == "F.Silkscreen"
    assert board.GetTitleBlock().GetTitle() == "Tiny Board"


def test_another_board_can_be_named_and_the_project_one_is_left_alone(
    project: Path, tmp_path: Path
) -> None:
    original = pcb_of(project).read_bytes()
    other = tmp_path / "other.kicad_pcb"
    shutil.copy(pcb_of(project), other)
    result = silk.apply_silk(load_project(project), other)
    assert result.pcb == other
    assert "TINY" in texts_of(load(other))
    assert pcb_of(project).read_bytes() == original


# --- the extra hook ----------------------------------------------------------------

HOOK = (
    SILK
    + '''

def extra(board, api):
    """Draw with the api, and check what the api says about it."""
    assert set(api.footprints) >= {"J1", "R1", "D1", "H1"}
    assert api.free(30.0, 3.0, "HOOK", 0.8)
    api.text("HOOK", 30.0, 3.0, 0.8)
    assert not api.free(30.0, 3.0, "HOOK", 0.8)  # what it drew is an obstacle now
    assert api.place("PL", [(30.0, 3.0), (30.0, 8.0, 0)], 0.8)  # first spot is taken
    assert not api.place("NO", [(30.0, 3.0)], 0.8)  # nothing free: nothing added
    api.warn("careful")
'''
)


def test_extra_draws_with_the_engines_state_and_its_texts_are_kept_clear_of(
    project: Path,
) -> None:
    (project / "silk.py").write_text(HOOK, encoding="utf-8")
    result = silk.apply_silk(load_project(project))
    texts = texts_of(load(result.pcb))
    assert "HOOK" in texts and "NO" not in texts
    (pl,) = texts["PL"]
    assert kb.to_local(pl.GetPosition()) == pytest.approx((30.0, 8.0), abs=1e-6)
    assert result.texts == 5  # TINY, V1, the connector label, HOOK and PL
    assert "careful" in result.warnings


def test_a_text_drawn_with_the_module_function_is_counted_and_guarded_too(
    project: Path,
) -> None:
    """A hook that bypasses ``api.text`` is still seen by the count and the guard."""
    source = SILK + (
        "\nfrom pcbkit.silk import add_text\n\n"
        "def extra(board, api):\n"
        "    add_text(board, 'RAW', 30.0, 3.0)\n"
    )
    (project / "silk.py").write_text(source, encoding="utf-8")
    result = silk.apply_silk(load_project(project))
    assert result.texts == 4
    assert "RAW" in texts_of(load(result.pcb))
    with pytest.raises(silk.SilkError, match="already carries"):
        silk.apply_silk(load_project(project))


# --- running the pass twice --------------------------------------------------------


def test_a_second_run_is_refused_and_writes_nothing(project: Path) -> None:
    proj = load_project(project)
    silk.apply_silk(proj)
    once = pcb_of(project).read_bytes()
    with pytest.raises(silk.SilkError) as refused:
        silk.apply_silk(proj)
    message = refused.value.message
    assert "tiny_board.kicad_pcb already carries silkscreen text" in message
    assert "3 of the 3 texts" in message
    assert "Nothing was written" in message
    assert isinstance(refused.value, click.ClickException)
    assert pcb_of(project).read_bytes() == once


def test_without_the_guard_the_second_run_draws_every_text_again(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the guard stops: the whole pass done again on a board that has had it.

    The second run lands each text exactly on the first one's. (KiCad 10.0.6 merges
    exact duplicates when it saves, so the file does not grow; the guard refuses
    because the pass has drawn everything again, whatever a given KiCad then does.)
    """
    proj = load_project(project)
    first = silk.apply_silk(proj)
    monkeypatch.setattr(silk, "stacked", lambda before, after: [])
    second = silk.apply_silk(proj)
    assert (first.texts, second.texts) == (3, 3)


def test_text_that_was_already_on_the_board_does_not_trip_the_guard(
    project: Path,
) -> None:
    """A text of the board's own (a logo, a note) is no reason to refuse."""
    board = load(pcb_of(project))
    kb.add_text(board, "NOTE", 5.0, 14.0, size=1.0)
    pcbnew.SaveBoard(str(pcb_of(project)), board)
    result = silk.apply_silk(load_project(project))
    texts = texts_of(load(result.pcb))
    assert "NOTE" in texts
    assert result.texts == 3  # the pass added three; the note was there before


def test_a_text_the_pass_would_draw_exactly_on_an_existing_one_is_refused(
    project: Path,
) -> None:
    board = load(pcb_of(project))
    kb.add_text(board, "TINY", 20.0, 20.0, size=1.0)
    pcbnew.SaveBoard(str(pcb_of(project)), board)
    before = pcb_of(project).read_bytes()
    with pytest.raises(silk.SilkError, match=r"1 of the 3 texts.*'TINY' at 20, 20"):
        silk.apply_silk(load_project(project))
    assert pcb_of(project).read_bytes() == before


# --- KEEP_REF, HIDE_REF and the messages --------------------------------------------


def test_a_kept_reference_is_left_as_its_footprint_has_it(project: Path) -> None:
    before = load(pcb_of(project)).FindFootprintByReference("R1").Reference()
    where = (before.GetPosition().x, before.GetPosition().y)
    layer = before.GetLayer()
    (project / "silk.py").write_text(SILK + 'KEEP_REF = {"R1"}\n', encoding="utf-8")
    board = load(silk.apply_silk(load_project(project)).pcb)
    after = board.FindFootprintByReference("R1").Reference()
    assert (after.GetPosition().x, after.GetPosition().y) == where
    assert after.GetLayer() == layer
    assert pcbnew.ToMM(after.GetTextHeight()) == pytest.approx(0.8)  # only the size
    assert layer_of(board.FindFootprintByReference("J1").Reference()) == "F.Silkscreen"


def test_a_reference_both_hidden_and_kept_is_a_message(project: Path) -> None:
    (project / "silk.py").write_text(SILK + 'KEEP_REF = {"H1"}\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="in both HIDE_REF and KEEP_REF: H1"):
        silk.apply_silk(load_project(project))


def test_a_connector_label_for_a_part_that_is_not_there_is_a_message(
    project: Path,
) -> None:
    (project / "silk.py").write_text('CONN_LABELS = {"Z9": "NOPE"}\n', encoding="utf-8")
    before = pcb_of(project).read_bytes()
    with pytest.raises(ProjectError, match="CONN_LABELS names Z9, which is not"):
        silk.apply_silk(load_project(project))
    assert pcb_of(project).read_bytes() == before


def test_a_hidden_reference_that_is_not_there_is_a_warning(project: Path) -> None:
    (project / "silk.py").write_text('HIDE_REF = {"H1", "Z9"}\n', encoding="utf-8")
    result = silk.apply_silk(load_project(project))
    assert result.warnings == ["silk.py: HIDE_REF names Z9, not on the board"]


def test_a_missing_board_says_to_build_first(project: Path) -> None:
    pcb_of(project).unlink()
    with pytest.raises(click.ClickException, match=r"not found: run `pcbkit build`"):
        silk.apply_silk(load_project(project))


# --- when there is no room -----------------------------------------------------------

# D1 in the corner with a big label over the room beside and below it: no spot is
# free for its reference, and the label itself collides with the board edge.
CORNER_LAYOUT = LAYOUT.replace('"HR1"', '"D1": (2.0, 1.5, 0),\n    "HR1"')
CROWDED = 'LABELS = [("WWWWWW", 6.0, 3.0, 3.0, 0)]\n'


def test_a_reference_with_no_room_goes_to_the_fab_layer_and_is_reported(
    project: Path,
) -> None:
    (project / "layout.py").write_text(CORNER_LAYOUT, encoding="utf-8")
    (project / "silk.py").write_text(CROWDED, encoding="utf-8")
    proj = load_project(project)
    place.place_board(proj)
    result = silk.apply_silk(proj)
    assert result.moved_to_fab == ["D1"]
    assert result.warnings == ["label collides: WWWWWW"]
    board = load(result.pcb)
    assert layer_of(board.FindFootprintByReference("D1").Reference()) == "F.Fab"
    assert layer_of(board.FindFootprintByReference("J1").Reference()) == "F.Silkscreen"
    # the label that collided is still drawn, at its own spot
    (label,) = texts_of(board)["WWWWWW"]
    assert kb.to_local(label.GetPosition()) == pytest.approx((6.0, 3.0), abs=1e-6)


def test_the_printed_result_lists_the_moved_references_and_the_warnings(
    project: Path,
) -> None:
    (project / "layout.py").write_text(CORNER_LAYOUT, encoding="utf-8")
    (project / "silk.py").write_text(CROWDED, encoding="utf-8")
    proj = load_project(project)
    place.place_board(proj)
    text = silk.format_result(silk.apply_silk(proj), proj.root)
    assert text.splitlines() == [
        "silk       kicad/tiny_board.kicad_pcb: 1 text(s) added",
        "  refs moved to fab (see assembly drawing): D1",
        "  warning: label collides: WWWWWW",
    ]
