"""Unit tests for the pure parts of pcbkit.check.copper: thickness and IPC-2221 rise.

The rest of the module rasterises a board and needs pcbnew. ``ipc2221_rise`` is checked
against IPC-2221's own formula, written out again here in the direction the standard
gives it: I = k * dT^0.44 * A^0.725, with the area A in square mils, k = 0.048 for an
outer layer and 0.024 for an inner one, and A = width * thickness (one ounce of
finished copper is 1.378 mil thick).
"""

from __future__ import annotations

import pytest

from pcbkit.check.copper import OZ_MM, ipc2221_rise, thickness_m

MIL_MM = 0.0254  # one mil, in millimetres
OZ_MIL = 1.378  # one ounce of copper, in mils


def ipc_current(rise_c: float, width_mm: float, oz: float, external: bool) -> float:
    """Return the current IPC-2221 allows for a rise of ``rise_c`` degrees."""
    k = 0.048 if external else 0.024
    area_mil2 = width_mm / MIL_MM * oz * OZ_MIL
    return k * rise_c**0.44 * area_mil2**0.725


# --- thickness_m ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("copper_mm", "metres"),
    [(0.035, 35e-6), (0.070, 70e-6), (0.0175, 17.5e-6), (0.105, 105e-6)],
)
def test_thickness_in_metres_is_exact_for_the_usual_weights(
    copper_mm: float, metres: float
) -> None:
    """Return 35e-6 for 0.035 mm, not the 3.5000000000000004e-05 of 0.035 * 1e-3."""
    assert thickness_m(copper_mm) == metres
    assert type(thickness_m(copper_mm)) is float


def test_without_the_rounding_0_035_mm_would_not_be_exactly_35_um() -> None:
    """Show that the exactness above comes from the rounding, not the arithmetic."""
    assert 0.035 * 1e-3 != 35e-6
    assert thickness_m(0.035) == 35e-6


def test_thickness_is_rounded_to_a_picometre() -> None:
    """Drop 0.4 pm and keep 0.6 pm (as one whole picometre) beyond 35 um."""
    assert thickness_m(0.0350000004) == 35e-6
    assert thickness_m(0.0350000006) == pytest.approx(35.000001e-6, rel=0, abs=1e-18)


def test_one_ounce_is_0_035_mm_and_two_ounces_are_twice_that() -> None:
    """Take OZ_MM as the thickness of one ounce of finished copper."""
    assert OZ_MM == 0.035
    assert thickness_m(2 * OZ_MM) == 70e-6
    assert thickness_m(0) == 0.0


# --- ipc2221_rise: values ------------------------------------------------------------


def test_one_ounce_external_trace_at_3_amps_in_1_mm_rises_16_7_degrees() -> None:
    """Work the case by hand and get 16.7 C.

    A = 1 / 0.0254 * 1.378 = 54.25 mil^2, A^0.725 = 18.09, 3 / (0.048 * 18.09) = 3.455
    and 3.455^(1 / 0.44) = 16.7.
    """
    area_mil2 = 1.0 / 0.0254 * 1.378
    assert area_mil2 == pytest.approx(54.25, abs=0.01)
    by_hand = (3.0 / (0.048 * area_mil2**0.725)) ** (1 / 0.44)
    assert by_hand == pytest.approx(16.74, abs=0.01)
    assert ipc2221_rise(3.0, 1.0, 0.035, True) == pytest.approx(by_hand, rel=1e-12)
    assert ipc2221_rise(3.0, 1.0) == pytest.approx(16.74, abs=0.01)  # the defaults


@pytest.mark.parametrize(
    ("amps", "width_mm", "copper_mm", "external"),
    [
        (3.0, 1.0, 0.035, True),
        (12.0, 4.0, 0.035, True),
        (6.0, 2.5, 0.070, True),
        (1.5, 0.3, 0.035, False),
        (8.0, 3.0, 0.0175, False),
    ],
)
def test_the_rise_gives_back_the_current_in_ipc_2221s_own_formula(
    amps: float, width_mm: float, copper_mm: float, external: bool
) -> None:
    """Put the rise back into I = k * dT^0.44 * A^0.725 and get the current back."""
    rise = ipc2221_rise(amps, width_mm, copper_mm, external)
    oz = copper_mm / 0.035
    assert ipc_current(rise, width_mm, oz, external) == pytest.approx(amps, rel=1e-9)


def test_no_current_means_no_rise() -> None:
    """Return 0 C for 0 A."""
    assert ipc2221_rise(0.0, 1.0) == 0.0


def test_the_defaults_are_one_ounce_and_an_outer_layer() -> None:
    """Take 0.035 mm of copper and an external conductor when not told otherwise."""
    assert ipc2221_rise(4.0, 2.0) == ipc2221_rise(4.0, 2.0, OZ_MM, True)
    assert ipc2221_rise(4.0, 2.0) == ipc2221_rise(4.0, 2.0, copper_mm=0.035)
    assert ipc2221_rise(4.0, 2.0) == ipc2221_rise(4.0, 2.0, external=True)


# --- ipc2221_rise: how it scales -----------------------------------------------------


def test_two_ounce_copper_runs_cooler_by_two_to_the_0_725_over_0_44() -> None:
    """Divide the rise by 2^(0.725 / 0.44), about 3.13, when the copper doubles."""
    one = ipc2221_rise(3.0, 1.0, 0.035)
    two = ipc2221_rise(3.0, 1.0, 0.070)
    factor = 2 ** (0.725 / 0.44)
    assert factor == pytest.approx(3.13, abs=0.01)
    assert one / two == pytest.approx(factor, rel=1e-12)
    assert two == pytest.approx(5.34, abs=0.01)


def test_half_an_ounce_runs_hotter_by_the_same_factor() -> None:
    """Multiply the rise by 2^(0.725 / 0.44) when the copper is halved."""
    half = ipc2221_rise(3.0, 1.0, 0.0175)
    one = ipc2221_rise(3.0, 1.0, 0.035)
    assert half / one == pytest.approx(2 ** (0.725 / 0.44), rel=1e-12)


def test_an_inner_layer_runs_hotter_by_two_to_the_1_over_0_44() -> None:
    """Halve k for an inner layer: the rise grows by 2^(1 / 0.44), about 4.83."""
    outer = ipc2221_rise(3.0, 1.0, external=True)
    inner = ipc2221_rise(3.0, 1.0, external=False)
    assert inner / outer == pytest.approx(2 ** (1 / 0.44), rel=1e-12)
    assert inner / outer == pytest.approx(4.83, abs=0.01)
    assert inner > outer


def test_the_rise_grows_with_current_as_the_current_to_the_1_over_0_44() -> None:
    """Rise more than fourfold when the current doubles, exactly 2^(1 / 0.44)."""
    assert ipc2221_rise(6.0, 2.0) / ipc2221_rise(3.0, 2.0) == pytest.approx(
        2 ** (1 / 0.44), rel=1e-12
    )


def test_a_wider_trace_runs_cooler_as_the_width_to_the_minus_0_725_over_0_44() -> None:
    """Divide the rise by 2^(0.725 / 0.44) when the width doubles, as for the copper."""
    narrow = ipc2221_rise(3.0, 1.0)
    wide = ipc2221_rise(3.0, 2.0)
    assert narrow / wide == pytest.approx(2 ** (0.725 / 0.44), rel=1e-12)


def test_the_rise_never_falls_as_the_current_grows() -> None:
    """Increase strictly with the current."""
    rises = [ipc2221_rise(amps, 1.0) for amps in (0.5, 1, 2, 3, 4, 5, 8, 12)]
    assert all(a < b for a, b in zip(rises, rises[1:]))


def test_the_rise_never_grows_as_the_trace_gets_wider_or_the_copper_thicker() -> None:
    """Decrease strictly with the width and with the copper thickness."""
    by_width = [ipc2221_rise(3.0, w) for w in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)]
    assert all(a > b for a, b in zip(by_width, by_width[1:]))
    by_copper = [ipc2221_rise(3.0, 1.0, c) for c in (0.0175, 0.035, 0.070, 0.105)]
    assert all(a > b for a, b in zip(by_copper, by_copper[1:]))
