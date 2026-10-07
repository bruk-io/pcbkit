"""Integration: the check engines read real boards built with pcbnew.

The engines in ``pcbkit/check`` have unit tests for their pure parts. These tests give
them real, saved boards (tests/check_boards.py builds small synthetic ones, with generic
nets such as PWR, GND and SIG) and compare what they report with the physics or the
geometry of what was built, worked out here by hand:

1. ``NetCopper``: the drop along a track is rho * L / (w * t), a thinner or narrower
   track drops more, a via or a plated hole joins the layers, and a broken net says so.
2. ``reserved``: copper of the wrong net inside a rectangle is found, with its layer and
   its area, and nothing else is.
3. ``stitching``: ground vias are counted, and the distance from a point to the nearest
   one is the distance of the lattice they were placed on.
4. ``coupling``: a victim track beside an aggressor is weighted by the 3H rule, the
   other layer counts at hypot(gap, h), and a pair of sense tracks encloses the area of
   its polygon.

These tests need KiCad's own Python, where ``import pcbnew`` works, and are skipped in
any other. Make that environment once and run them with it:

    KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
    uv venv --python $KICAD_PY --system-site-packages .venv-kicad
    VIRTUAL_ENV=.venv-kicad uv pip install -e . pytest
    .venv-kicad/bin/python -m pytest -m kicad -q tests/integration/test_check_engines.py

Every expected number is a formula written in the test, from the physics (rho * L /
(w * t), the 3H weight, a lattice's worst point at pitch / sqrt(2)) or from the geometry
that was built. A raster can only get within a cell of the truth; where a test allows
for that, the allowance is the cell, worked out in the test and never fitted to a
result.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_check_engines.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from PIL import Image  # noqa: E402

from pcbkit.check import coupling, reserved, stitching  # noqa: E402
from pcbkit.check.copper import NetCopper  # noqa: E402
from tests import check_boards as cb  # noqa: E402

pytestmark = pytest.mark.kicad

SIGNATURE = b"\x89PNG\r\n\x1a\n"
RHO = 1.72e-8  # ohm * m, copper at 20 C
OX = OY = 50.0  # KiCad file coordinates minus layout coordinates, mm


def load_in(tmp_path_factory: pytest.TempPathFactory, name: str, build: Any) -> Any:
    """Build a board with ``build(folder)`` and return it loaded, for a fixture."""
    return cb.load(build(tmp_path_factory.mktemp(name)))


# =============================================================================
# 1. NetCopper
# =============================================================================

FINE = 0.0125  # mm: the raster of the tests that check a resistance against a formula
ONE_OZ, TWO_OZ = 0.035, 0.070  # copper thickness, mm
PAD_TO_PAD = cb.TRACK_X[1] - cb.TRACK_X[0] - cb.POWER_PAD  # 28 mm of bare track


def strip_resistance(width_mm: float, length_mm: float, copper_mm: float) -> float:
    """Return rho * L / (w * t) in ohms for a strip; the lengths are in mm."""
    return RHO * (length_mm * 1e-3) / ((width_mm * 1e-3) * (copper_mm * 1e-3))


def raster_error(width_mm: float, res: float, length_mm: float = PAD_TO_PAD) -> float:
    """Return the relative error a raster of ``res`` mm can put on a strip's resistance.

    The rasteriser fills every cell a polygon touches, edges included, so a strip comes
    out wider than it is by one cell when its width is a whole number of cells, and by
    up to two otherwise; resistance goes as 1 / width, so it reads low by up to
    2 * res / width. The two pads at the ends are over-filled the same way, which
    shortens the bare track between them by up to a cell at each end.
    """
    return 2 * res / width_mm + 2 * res / length_mm


def drop(
    board: Any, net: str, pads: tuple[str, str], copper_mm: float, res: float
) -> float:
    """Return the voltage at the first pad when 1 A enters it and leaves the second."""
    copper = NetCopper(board, net, res=res, W=45.0, H=35.0, copper_mm=copper_mm)
    terminals = [
        (copper.pad_cells(pads[0], 1), 1.0),
        (copper.pad_cells(pads[1], 1), 0.0),
    ]
    return float(copper.solve(terminals)["v_terminals"][0])


@pytest.fixture(scope="module")
def power(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the board of wide, narrow and broken power tracks, and share it."""
    return load_in(tmp_path_factory, "power", cb.power_tracks)


@pytest.fixture(scope="module")
def drops(power: Any) -> dict[tuple[str, float], float]:
    """Solve the wide and the narrow track at 1 oz and at 2 oz on the fine raster."""
    wide = ("TP1", "TP2")
    narrow = ("TP3", "TP4")
    return {
        (net, copper_mm): drop(power, net, pads, copper_mm, FINE)
        for net, pads in (("/PWR_W", wide), ("/PWR_N", narrow))
        for copper_mm in (ONE_OZ, TWO_OZ)
    }


def test_a_two_millimetre_track_drops_rho_l_over_w_t_between_its_pads(
    drops: dict[tuple[str, float], float],
) -> None:
    """Match 1.72e-8 * 28 mm / (2 mm * 35 um) = 6.9 mohm per amp, to a raster cell."""
    expected = strip_resistance(cb.WIDE_MM, PAD_TO_PAD, ONE_OZ)
    assert expected == pytest.approx(6.88e-3, rel=1e-3)
    tolerance = raster_error(cb.WIDE_MM, FINE)
    assert drops["/PWR_W", ONE_OZ] == pytest.approx(expected, rel=tolerance)


def test_a_half_millimetre_track_drops_four_times_as_much(
    drops: dict[tuple[str, float], float],
) -> None:
    """Match 1.72e-8 * 28 mm / (0.5 mm * 35 um), and show the ratio of widths: 4."""
    expected = strip_resistance(cb.NARROW_MM, PAD_TO_PAD, ONE_OZ)
    assert expected == pytest.approx(27.5e-3, rel=1e-3)
    tolerance = raster_error(cb.NARROW_MM, FINE)
    assert drops["/PWR_N", ONE_OZ] == pytest.approx(expected, rel=tolerance)
    ratio = drops["/PWR_N", ONE_OZ] / drops["/PWR_W", ONE_OZ]
    both = raster_error(cb.WIDE_MM, FINE) + raster_error(cb.NARROW_MM, FINE)
    assert ratio == pytest.approx(cb.WIDE_MM / cb.NARROW_MM, rel=both)


@pytest.mark.parametrize("net", ["/PWR_W", "/PWR_N"])
def test_two_ounce_copper_halves_the_drop(
    drops: dict[tuple[str, float], float], net: str
) -> None:
    """Halve the drop when the copper is twice as thick, bar the pad ties' microvolts.

    The copper's resistance goes as 1 / t. The cells of a pad are tied together by a
    fixed 1000 S each, which does not change with the copper, and the whole current
    crosses at least w / res of those ties at each pad: that adds at most
    2 * I * res / (1000 S * w) to either drop, 12 uV for the wide track.
    """
    width = cb.WIDE_MM if net == "/PWR_W" else cb.NARROW_MM
    ties = 2 * 1.0 * FINE / (1e3 * width)
    assert drops[net, TWO_OZ] == pytest.approx(drops[net, ONE_OZ] / 2, abs=ties)
    expected = strip_resistance(width, PAD_TO_PAD, TWO_OZ)
    assert drops[net, TWO_OZ] == pytest.approx(expected, rel=raster_error(width, FINE))


def test_the_default_raster_finds_the_same_drop_to_its_coarser_cell(power: Any) -> None:
    """Solve with no raster size or step given, as the built-in checks do, to 0.1 mm."""
    copper = NetCopper(power, "/PWR_W")  # the outline plus 1 mm, 0.1 mm cells, 1 oz
    assert copper.res == 0.1
    assert copper.nx > 600 and copper.ny > 400  # the whole 60 x 40 mm board
    terminals = [(copper.pad_cells("TP1", 1), 1.0), (copper.pad_cells("TP2", 1), 0.0)]
    volts = copper.solve(terminals)["v_terminals"][0]
    expected = strip_resistance(cb.WIDE_MM, PAD_TO_PAD, ONE_OZ)
    assert volts == pytest.approx(expected, rel=raster_error(cb.WIDE_MM, 0.1))


def test_the_loss_in_the_copper_is_the_current_times_the_drop(power: Any) -> None:
    """Dissipate I * V in the track, less the sliver that goes in the pad ties.

    The ties (1000 S per cell) are not counted in the loss; with the current crossing
    w / res of them at each pad they take about 2 * I^2 * res / (1000 S * w), 0.7 % of
    the power for this track at 2 A, so the loss is within 1 % of I * V and below it.
    """
    amps = 2.0
    copper = NetCopper(power, "/PWR_W", res=0.05, W=45.0, H=35.0)
    terminals = [
        (copper.pad_cells("TP1", 1), amps),
        (copper.pad_cells("TP2", 1), 0.0),
    ]
    result = copper.solve(terminals)
    power_in = amps * result["v_terminals"][0]
    ties = 2 * amps**2 * 0.05 / (1e3 * cb.WIDE_MM)
    assert ties < 0.01 * power_in
    assert 0.99 * power_in <= result["loss_w"] <= power_in


def test_pad_cells_fill_the_pad_and_only_on_its_own_layer(power: Any) -> None:
    """Cover a 2 x 2 mm top-layer pad at (10, 10), one cell wider at most."""
    res = 0.1
    copper = NetCopper(power, "/PWR_W", res=res, W=45.0, H=35.0)
    cells = copper.pad_cells("TP1", 1)
    assert {layer for layer, _, _ in cells} == {pcbnew.F_Cu}
    assert len(set(cells)) == len(cells)
    rows = [row * res for _, row, _ in cells]
    cols = [col * res for _, _, col in cells]
    assert (min(cols), max(cols)) == pytest.approx((9.0, 11.0), abs=res)
    assert (min(rows), max(rows)) == pytest.approx((9.0, 11.0), abs=res)
    side = cb.POWER_PAD / res
    assert side * side <= len(cells) <= (side + 2) ** 2


def test_pad_cells_raises_for_a_pad_that_is_not_on_the_net(power: Any) -> None:
    """Refuse TP3, which is on /PWR_N, and a pad number the part does not have."""
    copper = NetCopper(power, "/PWR_W", res=0.1)
    with pytest.raises(ValueError, match=r"pad TP3\.1 has no copper on net /PWR_W"):
        copper.pad_cells("TP3", 1)
    with pytest.raises(ValueError, match=r"pad TP1\.2 has no copper on net /PWR_W"):
        copper.pad_cells("TP1", 2)


def test_solve_raises_when_a_terminal_is_cut_off_from_the_reference(power: Any) -> None:
    """Refuse a solve across the 2 mm break in /PWR_X: no copper joins its halves."""
    copper = NetCopper(power, "/PWR_X", res=0.1)
    terminals = [(copper.pad_cells("TP5", 1), 1.0), (copper.pad_cells("TP6", 1), 0.0)]
    with pytest.raises(ValueError, match="terminal not connected to reference"):
        copper.solve(terminals)


def test_heatmap_writes_a_png_with_a_map_in_it(power: Any, tmp_path: Path) -> None:
    """Save a PNG of the current per width, drawn: more than a blank figure."""
    copper = NetCopper(power, "/PWR_N", res=0.05, W=45.0, H=35.0)
    terminals = [(copper.pad_cells("TP3", 1), 1.0), (copper.pad_cells("TP4", 1), 0.0)]
    copper.solve(terminals)
    path = tmp_path / "heat.png"
    copper.heatmap(path, "A half millimetre track, 1 A")
    assert path.read_bytes().startswith(SIGNATURE)
    with Image.open(path) as image:
        width, height = image.size
        pixels = np.asarray(image.convert("RGB")).reshape(-1, 3)
    assert width > 500 and height > 200
    assert len(np.unique(pixels, axis=0)) > 50  # a colour map and bar, not a white page


# --- the joints between the layers --------------------------------------------------


def joint_drop(path: Path, net: str, pads: tuple[str, str], res: float) -> float:
    """Return the drop of 1 A from the first pad to the second, on a raster of ``res``.

    The cells under a joint are coupled all to all, so the work grows as the fourth
    power of 1 / res: a 1 mm plated hole is minutes of work on the 0.0125 mm raster of
    the track tests, and about a second on the 0.05 mm one used here.
    """
    board = cb.load(path)
    copper = NetCopper(board, net, res=res, W=30.0, H=35.0)
    terminals = [
        (copper.pad_cells(pads[0], 1), 1.0),
        (copper.pad_cells(pads[1], 1), 0.0),
    ]
    return float(copper.solve(terminals)["v_terminals"][0])


def test_a_via_carries_the_current_between_a_top_track_and_a_bottom_track(
    tmp_path: Path,
) -> None:
    """Drop the two tracks' resistance plus the barrel's: 1.1 mohm for a 0.4 mm drill.

    The barrel is a tube of 20 um plating and 1.6 mm length, R = rho * L / (pi * d * t).
    The current leaves each 0.6 mm track somewhere inside the 0.3 mm disc round the via,
    which makes the track between 1.2 mm (the near edge) and 1.5 mm (the centre) long
    from the 2 x 2 mm pad's edge. The raster may read each track up to two cells too
    wide or too narrow, a few percent of the tracks' resistance either way.
    """
    res = 0.025
    path = cb.layer_joints(tmp_path, via=True)
    volts = joint_drop(path, "/PWR_V", ("TP1", "TP2"), res)
    barrel = RHO * 1.6e-3 / (math.pi * 0.4e-3 * 20e-6)
    assert barrel == pytest.approx(1.1e-3, rel=0.01)
    pad_edge_to_via = cb.VIA_POINT[0] - (10.0 + cb.POWER_PAD / 2)  # 1.5 mm
    near_edge = pad_edge_to_via - (0.4 / 2 + 0.1)  # 1.2 mm
    per_mm = 2 * strip_resistance(
        cb.VIA_TRACK_MM, 1.0, ONE_OZ
    )  # both tracks, 1 mm each
    slack = 2 * res / cb.VIA_TRACK_MM
    lowest = per_mm * near_edge * (1 - slack) + barrel
    highest = per_mm * pad_edge_to_via * (1 + slack) + barrel
    assert lowest <= volts <= highest


def test_without_the_via_the_top_and_bottom_halves_are_not_joined(
    tmp_path: Path,
) -> None:
    """Refuse the solve when the only joint is gone: the layers do not touch."""
    path = cb.layer_joints(tmp_path, via=False)
    board = cb.load(path)
    copper = NetCopper(board, "/PWR_V", res=0.05, W=30.0, H=35.0)
    terminals = [(copper.pad_cells("TP1", 1), 1.0), (copper.pad_cells("TP2", 1), 0.0)]
    with pytest.raises(ValueError, match="terminal not connected to reference"):
        copper.solve(terminals)


def test_a_plated_through_hole_joins_the_layers_with_next_to_no_resistance(
    tmp_path: Path,
) -> None:
    """Drop only the two tracks' resistance, 3.15 mm of 1 mm copper each side.

    A soldered lead is taken as a near-ideal joint, so the path is the two tracks and
    a little pad copper (at most the pad's 0.85 mm half width of track each side), to
    within the raster's few percent of the tracks.
    """
    res = 0.05
    path = cb.layer_joints(tmp_path, via=True)
    volts = joint_drop(path, "/PWR_T", ("TP3", "TP4"), res)
    to_pad = (cb.PLATED_POINT[0] - 0.85) - (10.0 + cb.POWER_PAD / 2)  # 3.15 mm
    tracks = 2 * strip_resistance(cb.PLATED_TRACK_MM, to_pad, ONE_OZ)
    in_pad = 2 * strip_resistance(cb.PLATED_TRACK_MM, 0.85, ONE_OZ)
    slack = 2 * res / cb.PLATED_TRACK_MM
    assert tracks * (1 - slack) <= volts <= (tracks + in_pad) * (1 + slack)


# =============================================================================
# 2. reserved: copper inside a rectangle
# =============================================================================

PAD_SQUARE = 1.7  # the header's pad 1 is a 1.7 mm square, its pad 2 a 1.7 mm circle
VIA_MM = 0.8  # what kb.via draws by default


@pytest.fixture(scope="module")
def region(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the board with copper crossing the reserved rectangle, and share it."""
    return load_in(tmp_path_factory, "reserved", cb.reserved_region)


def found(
    board: Any, box: tuple[float, ...] = cb.REGION, **options: Any
) -> dict[Any, float]:
    """Return ``{(what, layer): area}`` for the copper ``copper_in_rect`` finds."""
    hits = reserved.copper_in_rect(board, *box, **options)
    assert len(hits) == len({(what, layer) for what, layer, _ in hits})
    return {(what, layer): area for what, layer, area in hits}


def crossing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the length of the straight run ``a`` to ``b`` inside the reserved box."""
    x0, y0, x1, y1 = cb.REGION
    (ax, ay), (bx, by) = a, b
    if ay == by:  # a horizontal run
        return max(0.0, min(max(ax, bx), x1) - max(min(ax, bx), x0))
    return max(0.0, min(max(ay, by), y1) - max(min(ay, by), y0))


def test_every_kind_of_copper_in_the_box_is_reported_with_net_layer_and_area(
    region: Any,
) -> None:
    """Report track, via, pad and pour: ``what`` ends in the net, the layer is named."""
    (sa, sb, sw), (ga, gb, gw) = cb.SIG_TRACK, cb.GND_TRACK
    via_area = math.pi * (VIA_MM / 2) ** 2
    expected = {
        ("track /SIG", "top"): sw * crossing(sa, sb),
        ("track /GND", "bottom"): gw * crossing(ga, gb),
        ("via /PWR", "top"): via_area,
        ("via /PWR", "bottom"): via_area,
        ("pad J1.1 /SIG", "top"): PAD_SQUARE**2,
        ("pad J1.1 /SIG", "bottom"): PAD_SQUARE**2,
        ("pad J1.2 /GND", "top"): math.pi * (PAD_SQUARE / 2) ** 2,
        ("pad J1.2 /GND", "bottom"): math.pi * (PAD_SQUARE / 2) ** 2,
    }
    assert crossing(sa, sb) == 10.0 and crossing(ga, gb) == 10.0
    hits = found(region)
    pour = hits.pop(("zone /GND", "bottom"))
    assert set(hits) == set(expected)  # no zone on the top layer, nothing else anywhere
    for key, area in expected.items():
        assert hits[key] == pytest.approx(area, abs=0.03), key
    # the filled pour, not its outline: the box less the cut-outs round other copper
    box = (cb.REGION[2] - cb.REGION[0]) * (cb.REGION[3] - cb.REGION[1])
    assert box - 12.0 < pour < box - PAD_SQUARE**2 - via_area


def test_a_track_outside_the_box_is_not_reported(region: Any) -> None:
    """Leave out the SIG track 2 mm below the box, though it is the same net."""
    below = found(region, (14.0, 21.0, 36.0, 23.0))  # a box round the outside track
    (a, b, w) = cb.OUTSIDE_TRACK
    whole_track = (
        w * (b[0] - a[0]) + math.pi * (w / 2) ** 2
    )  # the run and its round ends
    assert below[("track /SIG", "top")] == pytest.approx(whole_track, abs=0.01)
    inside = found(region)
    assert inside[("track /SIG", "top")] == pytest.approx(
        2.5, abs=0.01
    )  # the one track


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("top", {"track /SIG", "via /PWR", "pad J1.1 /SIG"}),
        ("bottom", {"via /PWR", "pad J1.1 /SIG"}),
    ],
)
def test_intruders_are_what_is_not_an_allowed_net(
    region: Any, layer: str, expected: set[str]
) -> None:
    """List the copper of other nets than /GND, with its area, on one layer only."""
    found_here = reserved.intruders(region, layer, cb.REGION, {"/GND"})
    assert {what for what, _ in found_here} == expected
    areas = dict(found_here)
    assert areas["pad J1.1 /SIG"] == pytest.approx(PAD_SQUARE**2, abs=0.03)
    assert all(area > reserved.MIN_AREA_MM2 for _, area in found_here)


def test_nothing_is_an_intruder_when_every_net_present_is_allowed(region: Any) -> None:
    """Return nothing when the allowed nets cover every net in the box."""
    everyone = {"/GND", "/SIG", "/PWR"}
    for layer in ("top", "bottom"):
        assert reserved.intruders(region, layer, cb.REGION, everyone) == []


def test_an_allowed_net_is_still_an_intruder_on_the_other_layer(region: Any) -> None:
    """Judge each layer alone: a pad allowed on one layer is a find on the other."""
    top = reserved.intruders(region, "top", cb.REGION, {"/SIG"})
    bottom = reserved.intruders(region, "bottom", cb.REGION, {"/SIG"})
    assert {what for what, _ in top} == {"via /PWR", "pad J1.2 /GND"}
    assert {what for what, _ in bottom} == {
        "via /PWR",
        "pad J1.2 /GND",
        "track /GND",
        "zone /GND",
    }


def test_skip_refs_leaves_a_footprints_pads_out_and_nothing_else(region: Any) -> None:
    """Drop the pads of J1 from the list, and every other kind of copper stays."""
    everything = found(region)
    skipped = found(region, skip_refs=("J1",))
    assert {k for k in everything if k[0].startswith("pad J1")} == {
        ("pad J1.1 /SIG", "top"),
        ("pad J1.1 /SIG", "bottom"),
        ("pad J1.2 /GND", "top"),
        ("pad J1.2 /GND", "bottom"),
    }
    assert set(skipped) == {k for k in everything if not k[0].startswith("pad J1")}
    assert found(region, skip_refs=("J2", "R1")) == everything  # refs it does not have


def test_overlaps_under_a_hundredth_of_a_square_millimetre_are_not_reported(
    region: Any,
) -> None:
    """Ignore 0.005 mm2 of the SIG track, report 0.02 mm2 of it, with its area."""
    x, y = 24.0, 14.9  # a corner in the middle of the track, which is 0.25 mm wide
    tiny = found(region, (x, y, x + 0.05, y + 0.1))  # 0.005 mm2, all of it in the track
    assert tiny == {}
    small = found(region, (x, y, x + 0.2, y + 0.1))  # 0.02 mm2
    assert small[("track /SIG", "top")] == pytest.approx(0.02, abs=0.005)
    assert reserved.MIN_AREA_MM2 == 0.01


def test_a_box_clear_of_everything_holds_only_the_bottom_pour(region: Any) -> None:
    """Find the pour and no other copper at the lower right, where nothing else is."""
    box = (40.0, 26.0, 55.0, 36.0)
    hits = found(region, box)
    assert set(hits) == {("zone /GND", "bottom")}
    assert hits[("zone /GND", "bottom")] == pytest.approx(150.0, abs=0.5)  # 15 x 10 mm
    assert reserved.intruders(region, "top", box, {"/GND"}) == []


# =============================================================================
# 3. stitching: ground vias and the distance to the nearest one
# =============================================================================

PITCH = cb.PITCH
# lambda / 20 at the top of the Wi-Fi band, 2.484 GHz, in an effective permittivity of
# (4.5 + 1) / 2 (half the field in FR4, half in air): 3.64 mm
RF_LIMIT = 299792458.0 / 2.484e9 / math.sqrt((4.5 + 1) / 2) * 1e3 / 20


def kicad_frame(points: list[tuple[float, float]]) -> Any:
    """Return layout points as an (n, 2) array of KiCad file millimetres."""
    return np.array([(x + OX, y + OY) for x, y in points])


def same_points(got: Any, expected: Any) -> None:
    """Assert two (n, 2) arrays hold the same points, in whatever order."""
    assert got.shape == expected.shape
    np.testing.assert_allclose(
        got[np.lexsort(got.T)], expected[np.lexsort(expected.T)], atol=1e-6
    )


def nearest_via(points: Any, vias: list[tuple[float, float]]) -> Any:
    """Return each point's distance to the nearest via, by brute force."""
    v = kicad_frame(vias)
    dx = points[:, 0][:, None] - v[:, 0][None, :]
    dy = points[:, 1][:, None] - v[:, 1][None, :]
    return np.hypot(dx, dy).min(axis=1)


def built(tmp_path_factory: pytest.TempPathFactory, name: str, **options: Any) -> Any:
    """Build a stitched board and return ``(board, stitched)`` for a module fixture."""
    stitched = cb.stitched_pours(tmp_path_factory.mktemp(name), name, **options)
    return cb.load(stitched.pcb), stitched


@pytest.fixture(scope="module")
def plain(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the completely stitched board: ground vias on a 5 mm lattice, two pours."""
    return built(tmp_path_factory, "plain")


@pytest.fixture(scope="module")
def island(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the lattice with a 3 x 3 block of its vias left out of the middle."""
    return built(tmp_path_factory, "island", gaps=[cb.ISLAND])


@pytest.fixture(scope="module")
def two_islands(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the lattice with that block left out, and one more via somewhere else."""
    return built(tmp_path_factory, "two", gaps=[cb.ISLAND, cb.LONE_GAP])


@pytest.fixture(scope="module")
def one_sided(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the lattice with no bottom pour, and no vias, from x = 40 mm rightwards."""
    return built(tmp_path_factory, "one_sided", one_sided=True)


def test_gnd_via_points_are_the_ground_vias_and_the_plated_ground_pads(
    tmp_path: Path,
) -> None:
    """Count three vias and two header pins on /GND, in KiCad file coordinates."""
    board = cb.load(cb.ground_points(tmp_path))
    hx, hy = cb.GROUND_HEADER
    pins = [(hx, hy), (hx, hy + 2.54)]  # a 1 x 2 header: the pins are 2.54 mm apart
    same_points(stitching.gnd_via_points(board), kicad_frame(cb.GROUND_VIAS + pins))


def test_gnd_via_points_leave_out_other_nets_and_pads_without_a_hole(
    tmp_path: Path,
) -> None:
    """Take SIG's own vias and header pins for /SIG, and no pad of the resistor R1."""
    board = cb.load(cb.ground_points(tmp_path))
    hx, hy = cb.SIGNAL_HEADER
    pins = [(hx, hy), (hx, hy + 2.54)]
    # R1's SIG pad is surface-mount, so it is not one of the four
    same_points(
        stitching.gnd_via_points(board, "/SIG"), kicad_frame(cb.SIGNAL_VIAS + pins)
    )
    assert len(stitching.gnd_via_points(board, "/NOT_A_NET")) == 0


def test_a_lattice_board_has_one_ground_point_per_via_placed(plain: Any) -> None:
    """Return 96 points for the 12 x 8 lattice, at the positions it was built on."""
    board, stitched = plain
    assert len(stitched.vias) == 12 * 8
    same_points(stitching.gnd_via_points(board), kicad_frame(stitched.vias))


def test_the_basis_is_lambda_over_twenty_and_the_lattices_worst_point() -> None:
    """Give 3.64 mm for the RF limit and 3.54 mm for a 5 mm lattice: it passes."""
    basis = stitching.Basis(PITCH)
    assert basis.rf_limit_mm == pytest.approx(RF_LIMIT)
    assert basis.grid_worst_mm == pytest.approx(PITCH / math.sqrt(2))
    assert PITCH / math.sqrt(2) < RF_LIMIT < 2 * PITCH / math.sqrt(2)


def test_the_worst_point_of_a_complete_lattice_is_pitch_over_root_two_away(
    plain: Any,
) -> None:
    """Find the centre of a cell, 5 / sqrt(2) = 3.54 mm from its four corner vias."""
    board, _ = plain
    worst, p99, d, pts, order = stitching.summarise(board)
    assert worst == pytest.approx(PITCH / math.sqrt(2), abs=1e-9)
    assert p99 <= PITCH / math.sqrt(2)
    assert worst < RF_LIMIT  # a 5 mm lattice meets lambda / 20 at 2.484 GHz
    assert d[order[0]] == worst and np.all(np.diff(d[order]) <= 0)  # worst first
    # the lattice sits on the 0.5 mm sample grid (see check_boards.PHASE), so a sample
    # lies on a via and another at a cell's centre: the exact worst point is sampled
    assert d.min() == pytest.approx(0.0, abs=1e-9)


def test_every_distance_is_the_distance_to_the_nearest_via_on_the_board(
    plain: Any,
) -> None:
    """Match a brute-force search over the vias that were placed, point by point."""
    board, stitched = plain
    d, pts = stitching.stitching_gaps(board)
    np.testing.assert_allclose(d, nearest_via(pts, stitched.vias), atol=1e-9)


def test_the_points_are_the_half_millimetre_grid_inside_both_pours(plain: Any) -> None:
    """Sample 0.5 mm apart over the pour, none outside it, in KiCad file coordinates."""
    board, _ = plain
    d, pts = stitching.stitching_gaps(board)
    x0, y0, x1, y1 = cb.POUR
    layout = pts - np.array([OX, OY])
    assert layout.min(axis=0) == pytest.approx((x0, y0), abs=stitching.GRID)
    assert layout.max(axis=0) == pytest.approx((x1, y1), abs=stitching.GRID)
    assert len(d) == pytest.approx((x1 - x0) * (y1 - y0) / stitching.GRID**2, rel=0.02)
    steps = np.unique(np.round(np.diff(np.unique(pts[:, 0])), 6))
    assert steps.tolist() == [stitching.GRID]


def test_a_complete_lattice_has_no_patch_beyond_the_rf_limit(plain: Any) -> None:
    """Report no patch, and no worst location, past lambda / 20 on the full lattice."""
    board, _ = plain
    _, _, d, pts, order = stitching.summarise(board)
    assert stitching.far_patches(d, pts, RF_LIMIT) == []
    assert stitching.worst_locations(d, pts, order, RF_LIMIT) == []


def test_a_stretch_with_ground_on_one_layer_only_is_not_sampled(one_sided: Any) -> None:
    """Skip the top-only pour: points count only where both layers carry ground fill."""
    board, stitched = one_sided
    assert len(stitched.vias) == 8 * 8  # the lattice, less its four right-hand columns
    worst, _, d, pts, _ = stitching.summarise(board)
    assert (pts[:, 0] - OX).max() < cb.BOTTOM_ONLY_FROM  # not one point beyond the cut
    assert (pts[:, 0] - OX).max() > cb.BOTTOM_ONLY_FROM - 1.0  # and right up to it
    assert worst == pytest.approx(PITCH / math.sqrt(2), abs=1e-9)  # no far-off point
    np.testing.assert_allclose(d, nearest_via(pts, stitched.vias), atol=1e-9)


def patch_extent_bounds(width: float) -> tuple[float, float]:
    """Return the least and most ``extent`` of a patch filling a region ``width`` wide.

    The patch is the points of the 0.5 mm grid inside the region. Their box is the
    region's width less up to two grid steps (one at each end), and the extent is that
    box's diagonal plus one step.
    """
    step = stitching.GRID
    low = math.hypot(width - 2 * step, width - 2 * step) + step
    return low, math.hypot(width, width) + step


def block_region_width(limit: float) -> float:
    """Return the width of the unstitched region round the 3 x 3 block's centre, in mm.

    The first via on each side of the block is 2 pitches from its centre. Half a pitch
    off a via row the nearest vias are two of that column, half a pitch above and below,
    so the region ends where their distance reaches ``limit``: sqrt(limit^2 - (P/2)^2)
    short of the column, on both sides of the block.
    """
    return 4 * PITCH - 2 * math.sqrt(limit**2 - (PITCH / 2) ** 2)


def lone_region_width(limit: float) -> float:
    """Return the width of the unstitched region round one missing via, in mm.

    Its four neighbours are a pitch away along the axes. The region's corner is where
    two of them are exactly ``limit`` away: (P - s)^2 + s^2 = limit^2, s off each axis.
    """
    return 2 * (PITCH - math.sqrt(2 * limit**2 - PITCH**2)) / 2


def test_leaving_out_a_block_of_vias_leaves_one_patch_at_its_centre(
    island: Any,
) -> None:
    """Find the missing block as one patch, centred on it, 2 pitches from any via."""
    board, _ = island
    worst, _, d, pts, order = stitching.summarise(board)
    assert worst == pytest.approx(2 * PITCH, abs=1e-9)
    assert stitching.local(pts[order[0]]) == pytest.approx(cb.ISLAND_CENTRE, abs=0.05)
    ((extent, farthest, centre),) = stitching.far_patches(d, pts, RF_LIMIT)
    assert farthest == pytest.approx(2 * PITCH, abs=0.01)
    assert centre == pytest.approx(cb.ISLAND_CENTRE, abs=0.1)
    width = block_region_width(RF_LIMIT)
    assert width == pytest.approx(14.7, abs=0.05)
    low, high = patch_extent_bounds(width)
    assert low < extent <= high


def test_worst_locations_name_the_missing_block_first_at_its_centre(
    island: Any,
) -> None:
    """Name the block's centre, 10 mm from a via, as the worst place, in layout mm."""
    board, _ = island
    _, _, d, pts, order = stitching.summarise(board)
    ((place, farthest),) = stitching.worst_locations(d, pts, order, RF_LIMIT, n=1)
    assert place == pytest.approx(cb.ISLAND_CENTRE, abs=0.05)
    assert farthest == pytest.approx(2 * PITCH, abs=0.005)
    several = stitching.worst_locations(d, pts, order, RF_LIMIT, n=5, sep=4.0)
    assert len(several) == 5
    distances = [dist for _, dist in several]
    assert distances == sorted(distances, reverse=True)
    assert all(dist > RF_LIMIT for dist in distances)
    for i, (a, _) in enumerate(several):
        for b, _ in several[i + 1 :]:
            assert math.dist(a, b) > 4.0  # at least ``sep`` apart


def test_two_gaps_make_two_patches_largest_first(two_islands: Any) -> None:
    """Report the block, then the one missing via, each with its centre and reach."""
    board, stitched = two_islands
    assert len(stitched.vias) == 12 * 8 - 9 - 1
    _, _, d, pts, order = stitching.summarise(board)
    big, small = stitching.far_patches(d, pts, RF_LIMIT)
    low, high = patch_extent_bounds(block_region_width(RF_LIMIT))
    assert low < big[0] <= high
    assert big[1] == pytest.approx(2 * PITCH, abs=0.01)
    assert big[2] == pytest.approx(cb.ISLAND_CENTRE, abs=0.1)
    # a lone gap is a via's distance from the next: 5 mm, at the gap itself
    assert small[1] == pytest.approx(PITCH, abs=0.01)
    assert small[2] == pytest.approx(cb.LONE_VIA, abs=0.1)
    assert (
        stitching.GRID < small[0] <= patch_extent_bounds(lone_region_width(RF_LIMIT))[1]
    )
    places = stitching.worst_locations(d, pts, order, RF_LIMIT, n=2, sep=12.0)
    assert [place for place, _ in places] == pytest.approx(
        [cb.ISLAND_CENTRE, cb.LONE_VIA], abs=0.05
    )
    assert [dist for _, dist in places] == pytest.approx([2 * PITCH, PITCH], abs=0.005)


# =============================================================================
# 4. coupling: the 3H weight, the other layer, the verdicts and the sense loops
# =============================================================================

H = 1.6  # board thickness, mm: the 3H window is 3 * H = 4.8 mm
STEP = 0.05  # the coupling raster's step, mm: Config.res
NEAR = {"V1": 0.4, "V2": 0.8, "V3": 1.6, "V4": 3.2, "V5": 4.7}  # within the window
FAR_NETS = ["T0", "T1", "T2", "B0", "B1"]  # of the far_layer board: within reach
SENSE_CLASS = {"sense": (0.01, 1.0e4, 5.0e6)}  # budget V, source ohm, dI/dt A/s


def weight(distance: float, h: float = H) -> float:
    """Return the 3H weight of a run ``distance`` mm off, edge to edge."""
    return 1.0 / (1.0 + (distance / h) ** 2)


def assert_weighted(
    effective: float, run: float, distance: float, h: float = H
) -> None:
    """Assert ``effective`` is ``run`` times the 3H weight at ``distance``, to a step.

    The raster puts each copper edge within one step of where it is, so the distance
    is known to a step either way, and the weight falls as the distance grows.
    """
    low = run * weight(distance + STEP, h)
    high = run * weight(max(distance - STEP, 0.0), h)
    assert low - 1e-9 <= effective <= high + 1e-9, (effective, low, high)


def current(*nets: str, **extra: Any) -> dict[str, coupling.Aggressor]:
    """Return one "cur" group of a current aggressor on ``nets``, as a Config has it."""
    return {"cur": coupling.Aggressor(tuple("/" + n for n in nets), "current", **extra)}


def sense(
    victims: list[str], aggressors: dict[str, coupling.Aggressor], **fields: Any
) -> coupling.Config:
    """Return a Config with every victim in one class and the given aggressor groups."""
    config = coupling.Config(
        classes=SENSE_CLASS,
        sensitive={"sense": ["/" + v for v in victims]},
        aggressors=aggressors,
        **fields,
    )
    assert config.res == STEP
    return config


@pytest.fixture(scope="module")
def ladder_board(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the board of a PWR track and six victims at growing gaps, and share it."""
    return load_in(tmp_path_factory, "ladder", cb.ladder)


@pytest.fixture(scope="module")
def ladder_result(ladder_board: Any) -> Any:
    """Return ``(config, analysis)`` of the ladder with a current aggressor on PWR."""
    config = sense(list(cb.LADDER_GAPS), current("PWR"))
    return config, coupling.analyse(ladder_board, config)


@pytest.mark.parametrize("net", NEAR)
def test_a_victim_inside_the_window_counts_its_whole_run_raw(
    ladder_result: Any, net: str
) -> None:
    """Report 30 mm raw for a 30 mm run beside the aggressor, at any gap in 4.8 mm."""
    config, analysis = ladder_result
    assert config.d_near == pytest.approx(3 * H)
    entry = analysis["/" + net]["cur"]
    assert entry["raw"] == pytest.approx(cb.RUN_MM, abs=STEP)
    assert entry["width"] == pytest.approx(cb.VICTIM_MM)
    assert entry["width_agg"] == pytest.approx(cb.AGG_MM)


@pytest.mark.parametrize("net", NEAR)
def test_the_effective_length_is_the_run_over_one_plus_gap_over_h_squared(
    ladder_result: Any, net: str
) -> None:
    """Weight the run by 1 / (1 + (gap / h)^2): 0.94 at 0.4 mm, 0.8 at 0.8, 0.5 at h."""
    _, analysis = ladder_result
    assert_weighted(analysis["/" + net]["cur"]["length"], cb.RUN_MM, NEAR[net])


def test_the_weight_is_a_half_when_the_gap_is_one_board_thickness(
    ladder_result: Any,
) -> None:
    """Halve the run at a 1.6 mm gap, where (d / h)^2 is 1: 15 mm of 30."""
    _, analysis = ladder_result
    assert NEAR["V3"] == H
    assert analysis["/V3"]["cur"]["length"] == pytest.approx(cb.RUN_MM / 2, rel=0.03)


def test_a_victim_past_the_window_has_nothing_to_report(
    ladder_board: Any, ladder_result: Any
) -> None:
    """Leave V6, 5 mm from the aggressor, out of the analysis: its entry is empty."""
    config, analysis = ladder_result
    assert cb.LADDER_GAPS["V6"] > 3 * H
    assert analysis["/V6"] == {}
    assert set(analysis) == {"/" + n for n in cb.LADDER_GAPS}
    only = coupling.analyse(ladder_board, config, nets=["/V2"])  # a chosen few
    assert set(only) == {"/V2"}


def test_the_box_of_a_run_is_in_layout_millimetres(ladder_result: Any) -> None:
    """Give the run's extent as x0, y0, x1, y1 on the layout's axes, not the file's."""
    _, analysis = ladder_result
    for net in NEAR:
        y = cb.ladder_y(net)
        assert analysis["/" + net]["cur"]["bbox"] == pytest.approx(
            [cb.VICTIM_X[0], y, cb.VICTIM_X[1], y], abs=0.11
        )


def test_the_field_holds_the_distance_to_the_aggressors_edge_per_layer(
    ladder_board: Any, ladder_result: Any
) -> None:
    """Give 0 inside the aggressor, the gap beside it, and None for a bare layer."""
    config, _ = ladder_result
    field = coupling.Field(ladder_board, coupling.extent_of(ladder_board), config)
    top = field.dist["cur", pcbnew.F_Cu]
    assert field.dist["cur", pcbnew.B_Cu] is None  # the group has no bottom copper
    assert field.width["cur"] == pytest.approx(cb.AGG_MM)

    def at(x: float, y: float) -> float:
        return float(top[round(y / STEP), round(x / STEP)])

    edge = cb.AGG_Y - cb.AGG_MM / 2  # the aggressor's upper edge
    assert at(30.0, cb.AGG_Y) == 0.0
    assert at(30.0, edge - 1.0) == pytest.approx(1.0, abs=STEP)
    assert at(30.0, edge - 7.0) == pytest.approx(7.0, abs=STEP)
    # past the aggressor's end: 3 mm along from the end of its centre line, 1.2 mm
    # across, less the radius of the track's round end
    beyond = cb.AGG_X[1] + 3.0
    round_end = math.hypot(3.0, 1.2) - cb.AGG_MM / 2
    assert at(beyond, edge - 1.0) == pytest.approx(round_end, abs=STEP)


def test_the_window_scales_with_the_board_thickness_read_from_the_spec(
    ladder_board: Any,
) -> None:
    """Take h from the stackup: at 3.2 mm V6's 5 mm gap is in, weighted by that h."""
    spec = {
        "classes": SENSE_CLASS,
        "sensitive": {"sense": ["V2", "V6"]},  # names written without the slash
        "aggressors": {"cur": {"nets": ["PWR"], "kind": "current"}},
    }
    thick = coupling.from_spec(spec, h_board=3.2)
    assert thick.d_near == pytest.approx(9.6)
    analysis = coupling.analyse(ladder_board, thick)
    gap6, gap2 = cb.LADDER_GAPS["V6"], cb.LADDER_GAPS["V2"]
    assert_weighted(analysis["/V6"]["cur"]["length"], cb.RUN_MM, gap6, 3.2)
    assert_weighted(analysis["/V2"]["cur"]["length"], cb.RUN_MM, gap2, 3.2)
    normal = coupling.analyse(ladder_board, coupling.from_spec(spec))
    assert normal["/V6"] == {}  # the 1.6 mm default, window 4.8 mm


# --- the aggressor's region ---------------------------------------------------------


def region_run(
    gap: float, start: float, stop: float, shift: float
) -> tuple[float, float]:
    """Return the raw and weighted length of a victim beside an aggressor cut short.

    The aggressor is cut to the strip ``start`` to ``stop`` along x (a flat cut), and
    the victim, 0.2 mm wide and ``gap`` mm from the strip's edge, runs well past both
    ends. A point ``s`` mm beyond the cut is hypot(s, gap + 0.1) from the strip's
    corner by centres, so the run within the window is the strip and two reaches of
    sqrt((window + 0.1)^2 - (gap + 0.1)^2). The weight is integrated along each reach
    on a fine step, with every distance moved by ``shift``.
    """
    half = cb.VICTIM_MM / 2
    reach = math.sqrt((3 * H + half) ** 2 - (gap + half) ** 2)
    s = np.linspace(0.0, reach, 20001)[1:]
    d = np.hypot(s, gap + half) - half + shift
    beyond = float((1.0 / (1.0 + (d / H) ** 2)).sum() * reach / 20000)
    inside = (stop - start) * weight(max(gap + shift, 0.0))
    return (stop - start) + 2 * reach, inside + 2 * beyond


def test_a_region_keeps_only_the_aggressor_copper_inside_it(ladder_board: Any) -> None:
    """Count the run within reach of a 10 mm cut of the aggressor, weighted."""
    gap = cb.LADDER_GAPS["V2"]
    config = sense(["V2"], current("PWR", region=(20.0, 0.0, 30.0, 40.0)))
    entry = coupling.analyse(ladder_board, config)["/V2"]["cur"]
    raw, _ = region_run(gap, 20.0, 30.0, 0.0)
    assert raw < cb.RUN_MM  # less than the run beside the whole aggressor
    assert entry["raw"] == pytest.approx(raw, abs=3 * STEP)
    _, near = region_run(gap, 20.0, 30.0, -STEP)
    _, far = region_run(gap, 20.0, 30.0, +STEP)
    assert far - 1e-6 <= entry["length"] <= near + 1e-6
    whole = coupling.analyse(ladder_board, sense(["V2"], current("PWR")))
    assert entry["length"] < whole["/V2"]["cur"]["length"]


def test_a_region_without_the_victim_in_reach_leaves_nothing(ladder_board: Any) -> None:
    """Report nothing for a region with copper too far from the victim, or none."""
    far_end = sense(["V2"], current("PWR", region=(0.0, 0.0, 8.0, 40.0)))
    assert coupling.analyse(ladder_board, far_end)["/V2"] == {}  # the cut ends 7 mm off
    elsewhere = sense(["V2"], current("PWR", region=(0.0, 30.0, 60.0, 40.0)))
    assert coupling.analyse(ladder_board, elsewhere)["/V2"] == {}  # no copper in it


# --- the other layer ----------------------------------------------------------------


@pytest.fixture(scope="module")
def far_result(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Return the analysis of the far-layer board with a current aggressor on PWR."""
    board = load_in(tmp_path_factory, "far", cb.far_layer)
    return coupling.analyse(board, sense(list(cb.FAR_LANES), current("PWR")))


def planar(net: str) -> float:
    """Return the planar edge-to-edge gap of a victim of the far-layer board."""
    return cb.FAR_LANES[net][1]


def test_an_aggressor_straight_below_counts_at_one_board_thickness(
    far_result: Any,
) -> None:
    """Weight a victim right above the aggressor at h: 1 / (1 + 1) = 0.5."""
    for net in ("T0", "B0"):  # the top victim and the bottom one
        entry = far_result["/" + net]["cur"]
        assert entry["raw"] == pytest.approx(cb.RUN_MM, abs=STEP)
        assert entry["length"] == pytest.approx(cb.RUN_MM * weight(H), rel=1e-6)


@pytest.mark.parametrize("net", FAR_NETS)
def test_the_other_layer_counts_at_the_hypotenuse_of_the_gap_and_h(
    far_result: Any, net: str
) -> None:
    """Weight a run by hypot(planar gap, h), for a top or a bottom victim alike."""
    gap = planar(net)
    low = cb.RUN_MM * weight(math.hypot(gap + STEP, H))
    high = cb.RUN_MM * weight(math.hypot(max(gap - STEP, 0.0), H))
    entry = far_result["/" + net]["cur"]
    assert low - 1e-9 <= entry["length"] <= high + 1e-9
    assert entry["raw"] == pytest.approx(cb.RUN_MM, abs=STEP)


def test_the_window_is_met_by_the_hypotenuse_on_the_other_layer(
    far_result: Any,
) -> None:
    """Put a victim 4.7 mm off in plan: on one layer that is in, on the other out."""
    assert planar("T3") < 3 * H < math.hypot(planar("T3"), H)  # 4.7 < 4.8 < 4.97
    assert far_result["/T3"] == {}
    assert math.hypot(planar("T2"), H) < 3 * H  # 4.2 mm off is 4.49 mm away: still in
    assert far_result["/T2"]["cur"]["raw"] == pytest.approx(cb.RUN_MM, abs=STEP)


# --- the kinds of aggressor and the verdicts ----------------------------------------


@pytest.fixture(scope="module")
def pair_board(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Build the board with a 1 mm AGG track and a long and a short victim beside it."""
    return load_in(tmp_path_factory, "pair", cb.violation_pair)


def grover(length_mm: float, distance_mm: float) -> float:
    """Return the mutual inductance (H) of two parallel filaments (Grover).

    M = (mu0 / 2 pi) * l * (asinh(l / d) - sqrt(1 + (d / l)^2) + d / l), l and d in m.
    """
    length, d = length_mm * 1e-3, distance_mm * 1e-3
    bracket = math.asinh(length / d) - math.sqrt(1 + (d / length) ** 2) + d / length
    return 2e-7 * length * bracket


def test_a_long_run_is_over_the_limit_and_a_short_one_is_not(pair_board: Any) -> None:
    """Flag LONG (27 mm effective) and not SHORT (2.7 mm) when the limit is 10 mm.

    The class's budget is the noise 10 mm of filament at the closest approach, the
    centres (0.2 + 1.0) / 2 = 0.6 mm apart, would induce at 5 A/us: by construction
    10 mm is the length at which the estimate reaches it.
    """
    di_dt = 5.0e6
    budget = grover(10.0, (cb.VICTIM_MM + cb.PAIR_AGG_MM) / 2) * di_dt
    config = coupling.Config(
        classes={"sense": (budget, 1.0e4, di_dt)},
        sensitive={"sense": ["/LONG", "/SHORT"]},
        aggressors=current("AGG"),
    )
    analysis = coupling.analyse(pair_board, config)
    long_run, short_run = analysis["/LONG"]["cur"], analysis["/SHORT"]["cur"]
    limit = coupling.limit(config, "/LONG", "cur", long_run)
    assert limit == pytest.approx(10.0, rel=1e-6)
    assert_weighted(long_run["length"], 30.0, cb.PAIR_GAP)
    assert_weighted(short_run["length"], 3.0, cb.PAIR_GAP)
    assert short_run["raw"] == pytest.approx(3.0, abs=STEP)
    (verdict,) = coupling.violations(config, analysis)
    net, group, effective, lmax, bbox = verdict
    assert (net, group, lmax) == ("/LONG", "cur", 10.0)
    assert effective == pytest.approx(30.0 * weight(cb.PAIR_GAP), rel=0.03)
    y = cb.pair_y("LONG")
    assert bbox == pytest.approx([15.0, y, 45.0, y], abs=0.11)
    # a filter downstream that cuts the pickup tenfold lets the long run through
    assert coupling.violations(config, analysis, credit={"/LONG": 10.0}) == []


def test_a_switching_node_is_judged_by_capacitance_not_inductance(
    pair_board: Any,
) -> None:
    """Take Lmax from the node's swing: 10 mm for a budget of 10 mm of the lower bound.

    A victim of source impedance Z sees Z * C' * dV/dt per millimetre if the edge is
    slower than its RC, and dV * C' / Cn if it is faster: the smaller of the two, here
    the first.
    """
    c_per_mm, dv, edge, cn, z = 0.05e-12, 8.5, 10e-9, 10e-12, 100.0
    per_mm = min(z * c_per_mm * dv / edge, dv * c_per_mm / cn)
    assert per_mm == pytest.approx(z * c_per_mm * dv / edge)
    config = coupling.Config(
        classes={"sense": (10.0 * per_mm, z, 1.0e6)},
        sensitive={"sense": ["/LONG", "/SHORT"]},
        aggressors={"sw": coupling.Aggressor(("/AGG",), "switch")},
        c_per_mm=c_per_mm,
        dv_sw=dv,
        dv_dt=dv / edge,
        cn_default=cn,
    )
    analysis = coupling.analyse(pair_board, config)
    (verdict,) = coupling.violations(config, analysis)
    assert verdict[:2] == ("/LONG", "sw")
    assert verdict[3] == pytest.approx(10.0, abs=0.05)
    assert 0 < analysis["/SHORT"]["sw"]["length"] < 10.0


def test_a_bar_is_only_the_wide_tracks_not_the_thin_ones(tmp_path: Path) -> None:
    """Count VA beside the 4 mm bar, not VB beside the 0.6 mm track of the same net."""
    board = cb.load(cb.bar_lanes(tmp_path))
    victims = list(cb.BAR_VICTIMS)
    bar = {"bar": coupling.Aggressor(("/BAR",), "bar")}
    config = sense(victims, bar)
    analysis = coupling.analyse(board, config)
    assert analysis["/VB"] == {}  # 0.6 mm is under the 3 mm a bar must be
    entry = analysis["/VA"]["bar"]
    assert entry["width_agg"] == pytest.approx(4.0)
    assert entry["raw"] == pytest.approx(cb.RUN_MM, abs=STEP)
    assert_weighted(entry["length"], cb.RUN_MM, cb.BAR_GAP)
    # the limit follows the bar's own width: centres (0.2 + 4.0) / 2 = 2.1 mm apart
    di_dt = 5.0e6
    budget = grover(15.0, (cb.VICTIM_MM + 4.0) / 2) * di_dt
    strict = coupling.Config(
        classes={"sense": (budget, 1.0e4, di_dt)},
        sensitive=config.sensitive,
        aggressors=bar,
    )
    assert coupling.limit(strict, "/VA", "bar", entry) == pytest.approx(15.0, rel=1e-6)
    assert [v[:2] for v in coupling.violations(strict, analysis)] == [("/VA", "bar")]
    # the same copper as a current aggressor: the thin track counts too
    both = coupling.analyse(board, sense(victims, current("BAR")))
    assert_weighted(both["/VA"]["cur"]["length"], cb.RUN_MM, cb.BAR_GAP)
    assert_weighted(both["/VB"]["cur"]["length"], cb.RUN_MM, cb.BAR_GAP)
    # and a bar must be as wide as the config says: at 5 mm the 4 mm track is not one
    wider = coupling.analyse(board, sense(victims, bar, bar_min_w=5.0))
    assert wider["/VA"] == {} and wider["/VB"] == {}


def test_a_one_millimetre_track_is_no_bar_so_nothing_is_flagged(
    pair_board: Any,
) -> None:
    """Find no copper in a bar group on the 1 mm aggressor, and so no violation."""
    bar = {"bar": coupling.Aggressor(("/AGG",), "bar")}
    config = sense(["LONG", "SHORT"], bar)
    analysis = coupling.analyse(pair_board, config)
    assert analysis == {"/LONG": {}, "/SHORT": {}}
    assert coupling.violations(config, analysis) == []


# --- the pair of sense nets ---------------------------------------------------------


def trapezoid(parallel_a: float, parallel_b: float, between: float) -> float:
    """Return the area of a trapezoid from its two parallel sides and their gap."""
    return (parallel_a + parallel_b) / 2 * between


def test_a_tight_pair_encloses_the_trapezoid_between_the_two_parts_pads(
    tmp_path: Path,
) -> None:
    """Enclose (1.65 + 1.27) / 2 * 27.5 mm = 40 mm2 for two straight tracks."""
    shunt = cb.shunt_pair(tmp_path, tight=True)
    board = cb.load(shunt.pcb)
    assert shunt.sa_from[0] == pytest.approx(shunt.sb_from[0])  # R1's pads, one above
    assert shunt.sa_to[0] == pytest.approx(shunt.sb_to[0])  # U1's pads 1 and 2, same
    assert shunt.sa_from[1] < shunt.sb_from[1] and shunt.sa_to[1] < shunt.sb_to[1]
    near = math.dist(shunt.sa_from, shunt.sb_from)
    far = math.dist(shunt.sa_to, shunt.sb_to)
    assert (near, far) == pytest.approx((1.65, 1.27))  # a 0603's pad pitch, a SOIC's
    run = shunt.sa_to[0] - shunt.sa_from[0]
    area, first, second = coupling.loop_area(
        board, ("SA", "SB"), "R1", "U1", ("1", "2")
    )
    assert area == pytest.approx(trapezoid(near, far, run), rel=1e-3)
    assert first == pytest.approx(math.dist(shunt.sa_from, shunt.sa_to), rel=1e-3)
    assert second == pytest.approx(math.dist(shunt.sb_from, shunt.sb_to), rel=1e-3)


def test_a_wide_loop_encloses_a_rectangle_and_a_step_and_far_more_than_a_pair(
    tmp_path: Path,
) -> None:
    """Enclose 24 x 20 mm and the 3.5 x 1.3 mm step to the pads: 484 mm2, 12 pairs."""
    tight = cb.shunt_pair(tmp_path / "tight", tight=True)
    wide = cb.shunt_pair(tmp_path / "wide", tight=False)
    board = cb.load(wide.pcb)
    left, right = wide.sa_from[0], wide.sa_to[0]
    rectangle = (cb.WIDE_X - left) * (cb.WIDE_BOTTOM - cb.WIDE_TOP)
    step = (right - cb.WIDE_X) * math.dist(wide.sa_to, wide.sb_to)
    area, first, second = coupling.loop_area(
        board, ("SA", "SB"), "R1", "U1", ("1", "2")
    )
    assert area == pytest.approx(rectangle + step, rel=1e-3)
    # each net runs out to the loop's edge, across, in to its pad's row, and to the pad
    across, last = cb.WIDE_X - left, right - cb.WIDE_X
    up = (wide.sa_from[1] - cb.WIDE_TOP) + (wide.sa_to[1] - cb.WIDE_TOP)
    down = (cb.WIDE_BOTTOM - wide.sb_from[1]) + (cb.WIDE_BOTTOM - wide.sb_to[1])
    assert first == pytest.approx(up + across + last, rel=1e-3)
    assert second == pytest.approx(down + across + last, rel=1e-3)
    tight_board = cb.load(tight.pcb)
    tight_area = coupling.loop_area(tight_board, ("SA", "SB"), "R1", "U1", ("1", "2"))[
        0
    ]
    assert area > 10 * tight_area


def test_only_the_pads_asked_for_on_the_far_part_make_the_loop(tmp_path: Path) -> None:
    """Take U1's pad 2 for SB, not pad 7 which is on SB too: 7 adds a 3 mm2 triangle."""
    shunt = cb.shunt_pair(tmp_path, tight=True)
    board = cb.load(shunt.pcb)
    near = math.dist(shunt.sa_from, shunt.sb_from)
    far = math.dist(shunt.sa_to, shunt.sb_to)
    run = shunt.sa_to[0] - shunt.sa_from[0]
    pair = coupling.loop_area(board, ("SA", "SB"), "R1", "U1", ("1", "2"))[0]
    other = coupling.loop_area(board, ("SA", "SB"), "R1", "U1", ("1", "7"))[0]
    triangle = 0.5 * far * (shunt.sb_extra[0] - shunt.sb_to[0])  # pad 7 is 4.95 mm on
    assert pair == pytest.approx(trapezoid(near, far, run), rel=1e-3)
    assert other == pytest.approx(pair + triangle, rel=1e-3)
    assert triangle == pytest.approx(3.14, abs=0.01)
    # the nets may be spelled as KiCad does, with the slash
    slashed = coupling.loop_area(board, ("/SA", "/SB"), "R1", "U1", ("1", "2"))
    assert slashed[0] == pytest.approx(pair)
