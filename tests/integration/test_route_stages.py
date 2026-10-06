"""Integration: the routing stages (rules, pre, post, eco) on a small board, real KiCad.

The board is tests/route_project.py: six parts on a 40 x 30 mm board, built from the
schematic that `pcbkit sch` writes. Its routing.py hooks make every stage do something
that a test can see, and the router's session file is written by the test, so nothing
here needs Freerouting (tests/e2e/test_route_real.py runs the router).

These tests need KiCad's own Python, where ``import pcbnew`` works, and kicad-cli, as in
tests/integration/test_kicad_core.py, whose header says how to make that environment.
"""

from __future__ import annotations

import json
import os
import re
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

import numpy  # noqa: E402, F401  (loaded now, so restored_imports never unloads it)
import scipy.spatial  # noqa: E402, F401

from pcbkit import sch  # noqa: E402
from pcbkit.kicad import board as kb  # noqa: E402
from pcbkit.project import Project, load_project  # noqa: E402
from pcbkit.route import eco as eco_stage  # noqa: E402
from pcbkit.route import post as post_stage  # noqa: E402
from pcbkit.route import pre as pre_stage  # noqa: E402
from pcbkit.route.files import route_files  # noqa: E402
from tests import route_project as rp  # noqa: E402
from tests.board_files import restored_imports  # noqa: E402

pytestmark = pytest.mark.kicad


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep a test's routing.py, layout.py and design.py from outliving it."""
    with restored_imports():
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the project, its schematic and its placed board once; tests copy them."""
    root = tmp_path_factory.mktemp("route_project")
    with restored_imports():
        proj = rp.write_project(root)
        assert sch.build_schematic(proj).erc.errors == 0
        rp.place(proj)
    return root


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Project:
    """Return a private copy of the built project."""
    root = tmp_path / "board"
    shutil.copytree(built, root)
    return load_project(root)


def events() -> list[str]:
    """Return the hook calls recorded by the project's routing.py so far."""
    import routing  # type: ignore[import-not-found]

    return list(routing.EVENTS)


def items(path: Path) -> tuple[list[Any], list[Any], list[Any]]:
    """Return a saved board's track segments, vias and rule areas."""
    board = pcbnew.LoadBoard(str(path))
    tracks = list(board.GetTracks())
    return (
        [t for t in tracks if t.GetClass() == "PCB_TRACK"],
        [t for t in tracks if t.GetClass() == "PCB_VIA"],
        [z for z in board.Zones() if z.GetIsRuleArea()],
    )


def ends(track: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return a track's two ends in layout millimetres, rounded to a micrometre."""
    a, b = (
        tuple(round(v, 3) for v in kb.to_local(p))
        for p in (track.GetStart(), track.GetEnd())
    )
    return a, b  # type: ignore[return-value]


def write_session(proj: Project) -> None:
    """Write a router session with the divider's wires and the LED's, as if routed."""
    pcb = route_files(proj).pcb

    def at(ref: str, num: int) -> tuple[float, float]:
        return rp.pad_file_position(pcb, ref, num)

    bend = (at("R3", 1)[0], at("J2", 1)[1])
    wires = {
        "/SENSE": [
            ("F.Cu", 0.25, [at("R2", 2), at("R3", 1)]),
            ("F.Cu", 0.25, [at("R3", 1), bend, at("J2", 1)]),
        ],
        "/LED_A": [("F.Cu", 0.25, [at("R1", 2), at("D1", 2)])],
    }
    route_files(proj).ses.write_text(rp.session_text(wires, {}), encoding="utf-8")


# --- pre ----------------------------------------------------------------------------


def test_pre_runs_the_hooks_in_order_and_writes_the_boards_and_the_dsn(
    project: Project,
) -> None:
    """Apply the rules, pre-route, add keep-outs; write both boards and the DSN."""
    result = pre_stage.pre(project)
    assert events() == ["design_rules", "prerouted", "keepouts"]
    files = route_files(project)
    assert (result.pcb, result.prerouted, result.dsn) == (
        files.pcb,
        files.prerouted,
        files.dsn,
    )
    for path in (files.pcb, files.prerouted, files.dsn):
        assert path.is_file(), path
    assert files.dsn.read_text(encoding="utf-8").startswith("(pcb kicad/my_board.dsn")


def test_pre_hand_routed_copper_is_locked_and_the_keepouts_are_all_there(
    project: Project,
) -> None:
    """Keep the hook's tracks locked, and add the hook's area and four edge strips."""
    pre_stage.pre(project)
    tracks, vias, areas = items(route_files(project).prerouted)
    assert len(tracks) == 3 and len(vias) == 1
    assert all(t.IsLocked() for t in tracks + vias)
    assert len(areas) == 5  # the project's one, and a strip on each of four edges

    def box(area: Any) -> tuple[float, float, float, float]:
        corners = area.Outline().COutline(0)
        points = [kb.to_local(corners.CPoint(i)) for i in range(corners.PointCount())]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return (
            round(min(xs), 3),
            round(min(ys), 3),
            round(max(xs), 3),
            round(max(ys), 3),
        )

    boxes = sorted(box(a) for a in areas)
    assert boxes == sorted(
        [
            (0, 0, 40, 0.45),  # top
            (0, 29.55, 40, 30),  # bottom
            (0, 0, 0.45, 30),  # left
            (39.55, 0, 40, 30),  # right
            (30, 4, 34, 6),  # the project's own
        ]
    )
    edge_strips = [a for a in areas if box(a) != (30, 4, 34, 6)]
    for strip in edge_strips:
        assert strip.GetDoNotAllowTracks() and strip.GetDoNotAllowVias()
        assert not strip.GetDoNotAllowZoneFills()  # pours may reach the edge


def test_pre_writes_the_net_classes_and_rules_into_the_project_file(
    project: Project,
) -> None:
    """Record the project's net class, the generic minimums and the hook's override."""
    pre_stage.pre(project)
    saved = json.loads(
        (route_files(project).kicad / "prerouted.kicad_pro").read_text("utf-8")
    )
    classes = {c["name"]: c for c in saved["net_settings"]["classes"]}
    assert classes["Default"]["track_width"] == 0.25
    assert classes["Power"]["track_width"] == 0.5
    assert classes["Power"]["clearance"] == 0.2
    assert classes["Power"]["via_diameter"] == 0.8
    assert {
        (p["pattern"], p["netclass"])
        for p in saved["net_settings"]["netclass_patterns"]
    } == {("/+3V3", "Power")}
    rules = saved["board"]["design_settings"]["rules"]
    assert rules["min_track_width"] == 0.15  # the project's design_rules hook won
    assert rules["min_clearance"] == 0.2
    assert rules["min_via_diameter"] == 0.6
    assert rules["min_through_hole_diameter"] == 0.2
    assert rules["min_copper_edge_clearance"] == 0.3
    assert rules["min_hole_clearance"] == 0.25
    assert rules["min_hole_to_hole"] == 0.5


def test_the_dsn_does_not_hold_the_path_of_the_machine_or_the_callers_directory(
    project: Project, tmp_path: Path
) -> None:
    """Export from the project root, whatever directory the command was run in."""
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        pre_stage.pre(project)
        assert Path(os.getcwd()).resolve() == tmp_path.resolve()  # put back after
    finally:
        os.chdir(here)
    first_line = route_files(project).dsn.read_text("utf-8").splitlines()[0]
    assert first_line == "(pcb kicad/my_board.dsn"
    assert str(tmp_path) not in route_files(project).dsn.read_text("utf-8")


def test_pre_without_a_placed_board_says_to_build_first(project: Project) -> None:
    """Fail with the command to run, before touching anything."""
    route_files(project).placed.unlink()
    with pytest.raises(click.ClickException, match=r"run `pcbkit build` first"):
        pre_stage.pre(project)
    assert not route_files(project).dsn.exists()


# --- post ---------------------------------------------------------------------------


def test_post_imports_the_session_and_removes_the_stub_and_the_lone_via(
    project: Project,
) -> None:
    """Show the clean-up working: stub and lone via present before, gone after."""
    pre_stage.pre(project)
    write_session(project)
    result = post_stage.post(project)
    files = route_files(project)
    assert result.ses_imported
    assert result.dropped == 2  # the stub and the one-sided via

    before_tracks, before_vias, _ = items(files.routed_nozones)
    stub = ((13.175, 20.0), (10.175, 20.0))
    assert stub in [ends(t) for t in before_tracks]
    assert [v.GetNetname() for v in before_vias] == ["/SENSE"]

    after_tracks, after_vias, _ = items(files.pcb)
    assert stub not in [ends(t) for t in after_tracks]
    assert "/SENSE" not in [v.GetNetname() for v in after_vias]
    # everything the stub was not stays: the power run and the session's wires
    kept = {(t.GetNetname(), ends(t)) for t in after_tracks}
    assert ("/+3V3", ((6.0, 8.0), (9.0, 8.0))) in kept
    assert ("/LED_A", ((14.825, 8.0), (22.787, 8.0))) in kept
    assert sum(1 for t in after_tracks if t.GetNetname() == "/SENSE") == 3


def test_post_runs_the_rules_and_the_hooks_in_order(project: Project) -> None:
    """Apply the rules, then gnd_links, then zones, after pre's own hook calls."""
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    assert events() == [
        "design_rules",
        "prerouted",
        "keepouts",
        "design_rules",
        "gnd_links",
        "zones",
    ]


def test_post_fills_a_ground_pour_on_each_layer(project: Project) -> None:
    """Fill both ground zones the hook made, and leave the rule areas unfilled."""
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    board = pcbnew.LoadBoard(str(route_files(project).pcb))
    pours = [z for z in board.Zones() if not z.GetIsRuleArea()]
    assert sorted((z.GetNetname(), z.GetLayer()) for z in pours) == sorted(
        [("/GND", pcbnew.F_Cu), ("/GND", pcbnew.B_Cu)]
    )
    for z in pours:
        assert z.GetNetname() == "/GND"
        assert z.HasFilledPolysForLayer(z.GetLayer())


def stitching_vias(path: Path) -> list[tuple[float, float]]:
    """Return the layout position of every ground via on the board."""
    _, vias, _ = items(path)
    return [
        tuple(round(v, 3) for v in kb.to_local(via.GetPosition()))  # type: ignore
        for via in vias
        if via.GetNetname() == "/GND"
    ]


def on_lattice(via: tuple[float, float], x0: float, y0: float, pitch: float) -> bool:
    """Return True if a via sits on the grid that starts at (x0, y0) with ``pitch``."""
    cells = ((via[0] - x0) / pitch, (via[1] - y0) / pitch)
    return all(abs(c - round(c)) < 1e-3 for c in cells)


def test_post_stitches_with_the_base_grid_and_the_dense_box(project: Project) -> None:
    """Put vias on the dense box's 2 mm lattice, and the base grid's 5 mm ones."""
    pre_stage.pre(project)
    write_session(project)
    result = post_stage.post(project)
    vias = stitching_vias(route_files(project).pcb)
    assert result.stitching_vias == len(vias)
    x0, y0, x1, y1, pitch = 22.0, 4.0, 38.0, 12.0, 2.0
    inside = [v for v in vias if x0 <= v[0] < x1 and y0 <= v[1] < y1]
    dense = [v for v in inside if on_lattice(v, x0, y0, pitch)]
    assert len(dense) >= 8
    # the base grid (2 + 5k, 2 + 5m) runs through the box too, off the dense lattice
    base = [v for v in inside if on_lattice(v, 2.0, 2.0, 5.0)]
    assert base and not set(base) & set(dense)
    assert any(not on_lattice(v, x0, y0, pitch) for v in vias)  # and outside the box


def test_the_dense_box_is_what_makes_the_lattice(project: Project) -> None:
    """Without [stitch] dense, no via lands on the lattice: the base grid is left."""
    plain = Path(project.root / "pcbkit.toml")
    plain.write_text(
        plain.read_text("utf-8").replace("dense = [[22.0, 4.0, 38.0, 12.0, 2.0]]", "")
    )
    proj = load_project(project.root)
    pre_stage.pre(proj)
    write_session(proj)
    post_stage.post(proj)
    vias = stitching_vias(route_files(proj).pcb)
    inside = [v for v in vias if 22.0 <= v[0] < 38.0 and 4.0 <= v[1] < 12.0]
    assert inside  # the base grid is still there
    assert not [v for v in inside if on_lattice(v, 22.0, 4.0, 2.0)]


def test_post_gives_the_solid_pad_refs_a_solid_ground_connection(
    project: Project,
) -> None:
    """Join J1's ground pad to the pour without spokes, and no other's."""
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    board = pcbnew.LoadBoard(str(route_files(project).pcb))

    def connection(ref: str, num: int) -> int:
        return int(kb.pad(board, ref, num).GetLocalZoneConnection())

    assert connection("J1", 2) == pcbnew.ZONE_CONNECTION_FULL
    assert connection("R3", 2) != pcbnew.ZONE_CONNECTION_FULL
    assert connection("J2", 2) != pcbnew.ZONE_CONNECTION_FULL


def test_post_writes_the_configured_copper_weight_last(project: Project) -> None:
    """Leave 0.07 mm, from pcbkit.toml, in a board that was placed with 0.035."""
    assert "(thickness 0.035)" in route_files(project).placed.read_text("utf-8")
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    text = route_files(project).pcb.read_text("utf-8")
    weights = re.findall(
        r'\(layer "[FB]\.Cu"\s*\(type "copper"\)\s*\(thickness ([0-9.]+)\)', text
    )
    assert weights == ["0.07", "0.07"]


def test_post_copies_the_bom_fields_from_design_py_onto_the_footprints(
    project: Project,
) -> None:
    """Set Description, MPN and Manufacturer, hidden, so DRC parity matches."""
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    board = pcbnew.LoadBoard(str(route_files(project).pcb))
    j1 = board.FindFootprintByReference("J1")
    assert j1.GetFieldText("Description") == "Supply header"
    assert j1.GetFieldText("MPN") == "HDR-2"
    assert j1.GetFieldText("Manufacturer") == "Acme"
    r1 = board.FindFootprintByReference("R1")
    assert r1.GetFieldText("Description") == "Resistor 330 0603 1%"
    assert r1.GetFieldText("MPN") == "0603 330 1%"
    assert not r1.GetField("MPN").IsVisible()
    assert not j1.GetField("Manufacturer").IsVisible()
    # R() gives no manufacturer, and none is invented
    assert not r1.HasField("Manufacturer")


def test_post_with_no_session_file_says_so_and_still_finishes(project: Project) -> None:
    """Report ses_imported False; the board has its pre-routes and no more."""
    pre_stage.pre(project)
    result = post_stage.post(project)
    assert not result.ses_imported
    assert result.summary().startswith("ses import False")
    tracks, _, _ = items(route_files(project).pcb)
    assert {t.GetNetname() for t in tracks} == {"/+3V3"}


def test_post_twice_in_one_process_gives_the_same_result(project: Project) -> None:
    """Survive the retry loop: removed items stay referenced and nothing breaks."""
    pre_stage.pre(project)
    write_session(project)
    first = post_stage.post(project)
    second = post_stage.post(project)
    assert (first.stitching_vias, first.dropped) == (
        second.stitching_vias,
        second.dropped,
    )
    assert first.ses_imported and second.ses_imported


def test_post_without_ground_pours_skips_stitching_instead_of_failing(
    project: Project,
) -> None:
    """Stitch only where there is a pour on both layers; here there is none."""
    path = project.root / "routing.py"
    text = path.read_text("utf-8")
    start = text.index("def zones(board, api):")
    path.write_text(text[:start] + "def zones(board, api):\n    pass\n", "utf-8")
    pre_stage.pre(project)
    write_session(project)
    result = post_stage.post(project)
    assert result.ses_imported
    assert result.stitching_vias == 0
    assert stitching_vias(route_files(project).pcb) == []


# --- eco ----------------------------------------------------------------------------


def make_golden(project: Project) -> Path:
    """Route the board by hand and keep its route in a golden-style folder."""
    pre_stage.pre(project)
    write_session(project)
    post_stage.post(project)
    files = route_files(project)
    golden = project.root / "golden"
    golden.mkdir()
    for source in (files.prerouted, files.ses, files.dsn):
        shutil.copyfile(source, golden / source.name)
    return golden


def move(project: Project, ref: str, x: float, y: float) -> None:
    """Place the board again with ``ref`` moved to (x, y)."""
    positions = dict(rp.layout_positions(project))
    positions[ref] = (x, y, positions[ref][2])
    rp.place(project, positions)


def test_eco_with_nothing_changed_keeps_every_routed_piece_locked(
    project: Project,
) -> None:
    """Copy the base route onto the same placement, leaving nothing free."""
    golden = make_golden(project)
    result = eco_stage.eco(project, golden)
    assert result.changed == ()
    assert result.kept == 4  # the three SENSE wires and the LED_A one; pre-routes recur
    assert result.unlocked == 0
    assert result.dropped == 0
    tracks, _, _ = items(route_files(project).prerouted)
    assert all(t.IsLocked() for t in tracks)


def test_eco_unlocks_copper_on_the_moved_parts_nets_and_near_them(
    project: Project,
) -> None:
    """Move R2: its nets' copper (and what lies within reach of it) is left free."""
    golden = make_golden(project)
    move(project, "R2", 14.0, 24.0)
    result = eco_stage.eco(project, golden)
    assert result.changed == ("R2",)
    assert result.unlocked >= 1
    assert result.kept + result.dropped >= 4
    tracks, _, _ = items(route_files(project).prerouted)
    loose = {t.GetNetname() for t in tracks if not t.IsLocked()}
    assert "/SENSE" in loose  # R2's sense pad moved: that net may be reworked
    summary = result.summary()
    assert "changed footprints: ['R2']" in summary
    assert f"kept {result.kept} routed pieces from {golden}" in summary


def test_eco_never_copies_ground_copper(project: Project) -> None:
    """Leave ground to the pours: copied ground fragments can hang the router."""
    golden = make_golden(project)
    # put a ground track into the base route, as if the router had made one
    base = pcbnew.LoadBoard(str(golden / "prerouted.kicad_pcb"))
    kb.track(base, [(35.0, 25.0), (37.0, 25.0)], 0.25, "GND")
    pcbnew.SaveBoard(str(golden / "prerouted.kicad_pcb"), base)
    eco_stage.eco(project, golden)
    tracks, _, _ = items(route_files(project).prerouted)
    assert "/GND" not in {t.GetNetname() for t in tracks}


def test_eco_drops_copied_copper_that_clashes_with_a_moved_part(
    project: Project,
) -> None:
    """Drop a base track that runs where the moved part now sits."""
    golden = make_golden(project)
    move(project, "R3", 21.0, 18.0)  # onto the SENSE wire along y = 18
    result = eco_stage.eco(project, golden)
    assert "R3" in result.changed
    assert result.dropped >= 1


def test_eco_exports_a_dsn_and_both_boards(project: Project) -> None:
    """Leave the same files as pre does, ready for the router."""
    golden = make_golden(project)
    for stale in (route_files(project).dsn, route_files(project).pcb):
        stale.unlink()
    eco_stage.eco(project, golden)
    files = route_files(project)
    assert files.dsn.read_text("utf-8").startswith("(pcb kicad/my_board.dsn")
    assert files.pcb.is_file() and files.prerouted.is_file()


@pytest.mark.parametrize("missing", ["prerouted.kicad_pcb", "my_board.ses"])
def test_eco_needs_a_golden_style_folder_with_both_files(
    project: Project, missing: str
) -> None:
    """Name the missing file and say what --eco needs."""
    golden = make_golden(project)
    (golden / missing).unlink()
    with pytest.raises(
        click.ClickException, match=r"--eco needs a folder like golden/"
    ):
        eco_stage.eco(project, golden)
