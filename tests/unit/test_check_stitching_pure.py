"""Unit tests for the pure parts of pcbkit.check.stitching.

``Basis`` (the numbers the stitching limits come from), ``local`` (KiCad file
millimetres to layout millimetres), ``worst_locations`` and ``far_patches``, on small
point sets built here. The points sit on the check's 0.5 mm sampling grid, and the
distance to the nearest ground via is whatever the test says it is. Reading a board
(``stitching_gaps`` and friends) needs pcbnew and is not covered here.

Points are in KiCad file millimetres, which are the layout's plus 50 in x and in y, so
a point at (60.0, 70.0) is at (10.0, 20.0) in the layout.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from pcbkit.check.stitching import Basis, far_patches, local, worst_locations

GRID = 0.5  # the check's sampling pitch, mm
LIGHT_SPEED = 299_792_458.0  # m/s


def grid(x0: float, y0: float, nx: int, ny: int) -> list[tuple[float, float]]:
    """Return an ``nx`` by ``ny`` block of points on the 0.5 mm grid from (x0, y0)."""
    return [(x0 + GRID * i, y0 + GRID * j) for j in range(ny) for i in range(nx)]


def patches(
    points: list[tuple[float, float]], dists: list[float], limit: float
) -> list[tuple[float, float, tuple[float, float]]]:
    """Return ``far_patches`` of the points, whose distances to a via are ``dists``."""
    return far_patches(np.array(dists), np.array(points), limit)


def ranked(
    points: list[tuple[float, float]], dists: list[float]
) -> tuple[Any, Any, Any]:
    """Return ``(d, pts, order)`` as ``worst_locations`` takes them, worst first."""
    d = np.array(dists)
    return d, np.array(points), np.argsort(-d)


# --- Basis ---------------------------------------------------------------------------


@pytest.mark.parametrize(("er_fr4", "er_eff"), [(4.5, 2.75), (1.0, 1.0), (3.0, 2.0)])
def test_the_effective_permittivity_takes_half_the_field_in_air(
    er_fr4: float, er_eff: float
) -> None:
    """Average the board's permittivity with 1, the air's: (er + 1) / 2."""
    assert Basis(5.0, er_fr4=er_fr4).er_eff == er_eff


def test_the_wavelength_at_the_top_of_the_wifi_band_is_about_72_8_mm() -> None:
    """Divide c by 2.484 GHz and by the square root of 2.75: 72.8 mm."""
    by_hand = LIGHT_SPEED / 2.484e9 / math.sqrt(2.75) * 1e3
    assert Basis(5.0).lambda_mm == pytest.approx(by_hand, rel=1e-12)
    assert Basis(5.0).lambda_mm == pytest.approx(72.8, abs=0.05)


def test_the_wavelength_follows_the_frequency_and_the_permittivity() -> None:
    """Give 299.79 mm in air at 1 GHz, and halve it at 2 GHz or at er_eff = 4."""
    air = Basis(5.0, f_hz=1e9, er_fr4=1.0)
    assert air.lambda_mm == pytest.approx(299.792458, rel=1e-12)
    assert Basis(5.0, f_hz=2e9, er_fr4=1.0).lambda_mm == pytest.approx(
        air.lambda_mm / 2, rel=1e-12
    )
    assert Basis(5.0, f_hz=1e9, er_fr4=7.0).lambda_mm == pytest.approx(
        air.lambda_mm / 2, rel=1e-12
    )  # (7 + 1) / 2 = 4, whose square root is 2


def test_the_rf_limit_is_a_twentieth_of_the_wavelength() -> None:
    """Allow lambda / 20, about 3.64 mm for the defaults."""
    basis = Basis(5.0)
    assert basis.rf_limit_mm == pytest.approx(basis.lambda_mm / 20, rel=1e-12)
    assert basis.rf_limit_mm == pytest.approx(3.64, abs=0.01)


@pytest.mark.parametrize(
    ("pitch", "worst"), [(5.0, 5.0 / math.sqrt(2)), (2.0, math.sqrt(2)), (1.0, 0.7071)]
)
def test_a_full_grid_leaves_its_worst_point_at_the_cell_centre(
    pitch: float, worst: float
) -> None:
    """Put the worst point of a square grid of pitch P at P / sqrt(2) from a via."""
    assert Basis(pitch).grid_worst_mm == pytest.approx(worst, rel=1e-4)
    assert (
        Basis(pitch, f_hz=5e9, er_fr4=3.0).grid_worst_mm == Basis(pitch).grid_worst_mm
    )


def test_a_grid_meets_the_rf_limit_only_up_to_a_pitch_of_root_two_times_it() -> None:
    """Fit a pitch of 5.1 mm (worst 3.61 mm) under the limit and not one of 5.2 mm."""
    limit = Basis(5.0).rf_limit_mm
    assert Basis(5.1).grid_worst_mm <= limit
    assert Basis(5.2).grid_worst_mm > limit
    assert Basis(limit * math.sqrt(2)).grid_worst_mm == pytest.approx(limit, rel=1e-12)


# --- local ---------------------------------------------------------------------------


def test_local_takes_the_50_mm_offset_off_and_rounds_to_a_tenth() -> None:
    """Convert a KiCad file position to layout millimetres, to 0.1 mm."""
    assert local((60.04, 70.06)) == (10.0, 20.1)
    assert local((50.0, 50.0)) == (0.0, 0.0)
    assert local((49.0, 120.0)) == (-1.0, 70.0)


def test_local_accepts_a_numpy_row_and_returns_plain_floats() -> None:
    """Return Python floats, which the results file can write, not numpy scalars."""
    result = local(np.array([75.26, 80.0]))
    assert result == (25.3, 30.0)
    assert [type(v) for v in result] == [float, float]


# --- worst_locations -----------------------------------------------------------------


def test_worst_locations_lists_the_worst_points_beyond_the_limit_spread_apart() -> None:
    """Skip a point within 4 mm of a worse one, and round the distance to 0.01 mm."""
    d, pts, order = ranked(
        [(60.0, 60.0), (61.0, 60.0), (70.0, 60.0), (80.0, 60.0)],
        [5.0, 6.12345, 4.5, 2.0],
    )
    assert worst_locations(d, pts, order, 3.0) == [
        ((11.0, 10.0), 6.12),
        ((20.0, 10.0), 4.5),
    ]


def test_worst_locations_stops_after_n_points() -> None:
    """Return at most ``n`` points, the worst first."""
    d, pts, order = ranked([(60.0, 60.0), (70.0, 60.0), (80.0, 60.0)], [4.0, 6.0, 5.0])
    assert worst_locations(d, pts, order, 3.0, n=2) == [
        ((20.0, 10.0), 6.0),
        ((30.0, 10.0), 5.0),
    ]
    assert worst_locations(d, pts, order, 3.0, n=1) == [((20.0, 10.0), 6.0)]
    assert len(worst_locations(d, pts, order, 3.0)) == 3


def test_worst_locations_spacing_can_be_changed() -> None:
    """Report two points 1 mm apart when ``sep`` is under 1 mm, not when it is 4 mm."""
    d, pts, order = ranked([(60.0, 60.0), (61.0, 60.0)], [5.0, 6.0])
    assert worst_locations(d, pts, order, 3.0, sep=4.0) == [((11.0, 10.0), 6.0)]
    assert worst_locations(d, pts, order, 3.0, sep=0.5) == [
        ((11.0, 10.0), 6.0),
        ((10.0, 10.0), 5.0),
    ]


def test_a_point_at_the_limit_is_not_beyond_it() -> None:
    """Report nothing when the worst distance equals the limit, one point above it."""
    d, pts, order = ranked([(60.0, 60.0)], [3.0])
    assert worst_locations(d, pts, order, 3.0) == []
    d, pts, order = ranked([(60.0, 60.0)], [3.0001])
    assert worst_locations(d, pts, order, 3.0) == [((10.0, 10.0), 3.0)]


def test_worst_locations_of_nothing_is_empty() -> None:
    """Return an empty list when every point is within the limit."""
    d, pts, order = ranked([(60.0, 60.0), (70.0, 60.0)], [1.0, 2.0])
    assert worst_locations(d, pts, order, 3.0) == []


# --- far_patches ---------------------------------------------------------------------


def test_a_block_of_far_points_is_one_patch_and_near_points_do_not_join_it() -> None:
    """Make a 3 by 3 block one patch with its diagonal plus one grid step across.

    The 5 by 5 field has a ring of near points around the block. The extent is
    hypot(1.0, 1.0) + 0.5 = 1.91 mm, the largest distance 7.25 and the centre the
    block's middle.
    """
    points = grid(59.5, 59.5, 5, 5)
    dists = []
    for x, y in points:
        inside = 60.0 <= x <= 61.0 and 60.0 <= y <= 61.0
        dists.append(5.0 if inside else 1.0)
    dists[points.index((60.5, 60.5))] = 7.25
    assert patches(points, dists, 3.0) == [(1.91, 7.25, (10.5, 10.5))]


def test_a_grid_that_starts_off_a_whole_millimetre_still_joins_up() -> None:
    """Join the points of a 3 by 3 block on the grid a board edge's stroke produces.

    A board outline drawn with a 0.05 mm stroke starts 0.025 mm outside its corner, so
    the sample points sit at 50.225, 50.725 ... rather than on whole half millimetres.
    """
    points = grid(60.225, 60.225, 3, 3)
    assert patches(points, [5.0] * 9, 3.0) == [(1.91, 5.0, (10.7, 10.7))]


def test_a_single_far_point_is_a_patch_one_grid_step_across() -> None:
    """Give an extent of 0.5 mm for one point, at its own position."""
    assert patches([(70.0, 80.0)], [4.0], 3.0) == [(0.5, 4.0, (20.0, 30.0))]


def test_separate_patches_are_listed_largest_first_in_any_input_order() -> None:
    """Sort by extent: a 3 by 3 block (1.91), a row of 3 (1.5), a lone point (0.5)."""
    block = grid(60.0, 60.0, 3, 3)
    row = grid(70.0, 60.0, 3, 1)
    lone = [(80.0, 60.0)]
    points = lone + row + block  # smallest first, to show the sorting
    dists = [9.0] + [4.0] * 3 + [5.0] * 9
    assert patches(points, dists, 3.0) == [
        (1.91, 5.0, (10.5, 10.5)),
        (1.5, 4.0, (20.5, 10.0)),
        (0.5, 9.0, (30.0, 10.0)),
    ]


def test_points_that_touch_only_at_a_corner_are_one_patch() -> None:
    """Join diagonal neighbours (8-connectivity): three on a diagonal make one patch."""
    points = [(60.0, 60.0), (60.5, 60.5), (61.0, 61.0)]
    assert patches(points, [5.0] * 3, 3.0) == [(1.91, 5.0, (10.5, 10.5))]


def test_points_two_grid_steps_apart_are_two_patches() -> None:
    """Keep (60, 60) and (61, 60) apart: nothing on the grid lies between them."""
    result = patches([(60.0, 60.0), (61.0, 60.0)], [5.0, 5.0], 3.0)
    assert sorted(result) == [(0.5, 5.0, (10.0, 10.0)), (0.5, 5.0, (11.0, 10.0))]


def test_a_near_point_in_a_row_splits_it_in_two() -> None:
    """Cut a row of seven in two rows of three by one point within the limit."""
    points = grid(60.0, 60.0, 7, 1)
    result = patches(points, [5.0, 5.0, 5.0, 1.0, 5.0, 5.0, 5.0], 3.0)
    assert sorted(result) == [(1.5, 5.0, (10.5, 10.0)), (1.5, 5.0, (12.5, 10.0))]


def test_a_point_at_the_limit_is_not_far() -> None:
    """Count a point as far only when its distance is over the limit."""
    assert patches([(60.0, 60.0)], [3.0], 3.0) == []
    assert patches([(60.0, 60.0)], [3.0001], 3.0) == [(0.5, 3.0, (10.0, 10.0))]


@pytest.mark.parametrize(
    ("points", "extent"),
    [
        (grid(60.0, 60.0, 5, 1), 2.5),  # a row 2.0 mm long, plus one step
        (grid(60.0, 60.0, 1, 5), 2.5),  # the same, upright
        (grid(60.0, 60.0, 3, 2), 1.62),  # hypot(1.0, 0.5) + 0.5
        (grid(60.0, 60.0, 3, 3), 1.91),  # hypot(1.0, 1.0) + 0.5
        (grid(60.0, 60.0, 1, 1), 0.5),
    ],
)
def test_the_extent_is_the_diagonal_of_the_patch_plus_one_grid_step(
    points: list[tuple[float, float]], extent: float
) -> None:
    """Measure a patch across its bounding box, one grid step more than the points."""
    result = patches(points, [5.0] * len(points), 3.0)
    assert [row[0] for row in result] == [extent]


def test_the_largest_distance_of_a_patch_is_rounded_to_a_hundredth() -> None:
    """Report the worst point of the patch, 4.12 for 4.12345."""
    points = grid(60.0, 60.0, 3, 1)
    assert patches(points, [3.5, 4.12345, 3.9], 3.0) == [(1.5, 4.12, (10.5, 10.0))]


def test_the_centre_is_the_mean_of_the_points_not_the_middle_of_the_box() -> None:
    """Put the centre of an uneven patch at the mean of its points, in layout mm.

    The four points have x 60, 60.5, 61, 60 and y 60, 60, 60, 60.5: mean (60.375,
    60.125), which is (10.4, 10.1) in the layout; their box is centred on (60.5, 60.25).
    """
    points = [(60.0, 60.0), (60.5, 60.0), (61.0, 60.0), (60.0, 60.5)]
    assert patches(points, [5.0] * 4, 3.0) == [(1.62, 5.0, (10.4, 10.1))]


def test_far_patches_of_nothing_is_empty() -> None:
    """Return an empty list when no point is beyond the limit, or there are none."""
    points = grid(60.0, 60.0, 3, 3)
    assert patches(points, [1.0] * 9, 3.0) == []
    assert far_patches(np.array([]), np.empty((0, 2)), 3.0) == []
