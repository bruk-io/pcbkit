"""Integration: the ``copper`` check group on a real board, as `pcbkit check` runs it.

A small generic project is written to disk: a ``pcbkit.toml``, a ``specs.py``, the
netlist (served by a ``fake_nl`` plugin, so no schematic is needed) and a real 60 x 40
mm board built with pcbnew (tests/check_group_boards.py): a ground stitched on a 5 mm
lattice, a 3 mm supply (VIN) and return (RTN), a sensitive net (SENSE) beside an
aggressor bar (PWR) with an RC filter on it, a shunt's sense pair, and two regions
reserved for ground. The real command line (``pcbkit.check.runner.command``) runs the
group in a child pytest and ``out/checks/results.json`` is read back.

The control project passes all twelve checks of the group. Each check is then shown to
bite by planting one mistake in the control (a track, a via, a name, a width, a number
in the specs) and watching exactly the check that guards it fail, with the message and
the numbers that say why. ``test_lmax_basis`` is pure maths with nothing in the project
to get wrong, so it is shown to pass and to agree with a worked example.

Every expected number is worked out here from the plan (the geometry built, the
budgets written in the specs) with the physics written out again in this file (rho * L /
(w * t), IPC-2221, Grover's mutual inductance, the 3H weight, lambda / 20), never copied
from what a check printed. The copper solver rasterises on 0.1 mm cells that fill every
cell a shape touches, so a resistance can read low by up to two cells of width and a
cell of length; where a test allows for that, the allowance is worked out in the test.

These need KiCad's own Python (``import pcbnew``) to build the boards: see
tests/integration/test_kicad_core.py for how to make ``.venv-kicad``. The checks run in
a child process of the same Python, so no kicad-cli is needed but for the DRC of the
control board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pcbnew = pytest.importorskip(
    "pcbnew",
    reason="pcbnew only imports under KiCad's own Python: see tests/integration/"
    "test_kicad_core.py or .claude/CLAUDE.md for how to make .venv-kicad",
)

from pcbkit.kicad import cli  # noqa: E402
from tests import check_group_boards as gb  # noqa: E402
from tests.check_group_run import (  # noqa: E402
    Lab,
    Result,
    replace_once,
    write_netlist,
    write_toml,
)

pytestmark = pytest.mark.kicad

MODULE = "pcbkit.check.builtin.test_copper"

# --- the plan: budgets and limits written into the specs ------------------------------

I_CONT, I_PEAK = 4.0, 8.0  # A: the heating test current, and the current of the drops
MAX_RISE_C = 20.0
DROP_MAX_V = 0.08  # V per segment, at I_PEAK
NOMINAL_V, MAX_FRACTION = 12.0, 0.01  # the far load may lose 1 % of 12 V: 120 mV
LOAD_SHARES = {"near": 0.4, "far": 0.6}  # of the test current, in both segments
LMAX_MM = 12.0  # how far SENSE may run beside PWR at the closest approach, by design
DI_DT = 5.0e6  # A/s of the class "sense"
T_EDGE = 10e-6  # s: the edge the filter on SENSE has to smooth
FILTER_R, FILTER_C = 10e3, 100e-9  # R10 and C1 in the netlist: tau is 1 ms
LOOP_MAX_MM2 = 60.0
CAP_MAX_MM = 5.0
RES = 0.1  # mm: the copper solver's raster
RHO = 1.72e-8  # ohm m, copper at 20 C
H_BOARD = 1.6  # mm: [stackup] thickness_mm by default, the 3H rule's h
STEP = 0.05  # mm: the coupling raster's step
OZ = 0.035  # mm of copper in one ounce

CONTROL = {
    "test_reserved_copper_stays_clear[return_strip]",
    "test_reserved_copper_stays_clear[rf_keepout]",
    "test_stitching_basis",
    "test_stitching_density",
    "test_sensitive_nets_exist",
    "test_lmax_basis",
    "test_parallel_length[/SENSE]",
    "test_filter_caps_sit_by_the_adc_pin[/SENSE]",
    "test_sense_pair_loop_area[shunt]",
    "test_copper_segment[vin_path]",
    "test_copper_segment[rtn_path]",
    "test_load_supply_regulation",
}


# --- the physics, written out again ---------------------------------------------------


def strip_resistance(width_mm: float, length_mm: float, copper_mm: float) -> float:
    """Return rho * L / (w * t) in ohms for a strip; the lengths are in mm."""
    return RHO * (length_mm * 1e-3) / ((width_mm * 1e-3) * (copper_mm * 1e-3))


def resistance_band(
    width_mm: float, length_mm: float, copper_mm: float
) -> tuple[float, float]:
    """Return the least and most the raster can read for a strip of copper.

    Every cell a shape touches is filled, so a strip comes out up to two cells wider
    than it is (resistance goes as 1 / width) and each pad end up to a cell too long,
    which shortens the bare strip between two pads by up to a cell at each end.
    """
    most = strip_resistance(width_mm, length_mm, copper_mm)
    least = strip_resistance(width_mm + 2 * RES, length_mm - 2 * RES, copper_mm)
    return least, most


def ipc_rise(amps: float, width_mm: float, copper_mm: float = OZ) -> float:
    """Return the rise (C) IPC-2221 gives an external trace: I = 0.048 dT^0.44 A^0.725.

    A is the cross-section in square mils: the width in mils times the copper in mils
    (1 oz is 1.378 mil).
    """
    area = width_mm / 0.0254 * (copper_mm / OZ) * 1.378
    return (amps / (0.048 * area**0.725)) ** (1 / 0.44)


def grover(length_mm: float, centres_mm: float) -> float:
    """Return the mutual inductance (H) of two parallel filaments (Grover).

    M = (mu0 / 2 pi) * l * (asinh(l / d) - sqrt(1 + (d / l)^2) + d / l), l and d in m.
    """
    length, d = length_mm * 1e-3, centres_mm * 1e-3
    return (
        2e-7
        * length
        * (math.asinh(length / d) - math.hypot(1, d / length) + d / length)
    )


def length_at_noise(budget_v: float, centres_mm: float) -> float:
    """Return the length (mm) at which Grover's noise reaches ``budget_v`` (volts)."""
    low, high = 1e-3, 1e4
    for _ in range(100):
        middle = math.sqrt(low * high)
        if grover(middle, centres_mm) * DI_DT < budget_v:
            low = middle
        else:
            high = middle
    return low


def weight(distance_mm: float) -> float:
    """Return the 3H weight of copper ``distance_mm`` away, edge to edge."""
    return 1.0 / (1.0 + (distance_mm / H_BOARD) ** 2)


def to_segment(p: gb.Point, a: gb.Point, b: gb.Point) -> float:
    """Return the distance from ``p`` to the segment ``a`` to ``b``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def coupled(
    victim: list[gb.Point], bar: tuple[gb.Point, gb.Point], slack: float
) -> tuple[float, float]:
    """Return the raw and the weighted length (mm) of SENSE beside the bar PWR.

    Walk the victim's path in 0.01 mm steps; a point counts while its edge-to-edge
    distance to the bar, ``slack`` more than the geometry says, is inside 3 h, weighted
    by 1 / (1 + (d / h)^2). The raster places each edge within a step or two of where
    it is, which is what ``slack`` allows for.
    """
    raw = effective = 0.0
    for a, b in zip(victim[:-1], victim[1:]):
        n = max(1, round(math.dist(a, b) / 0.01))
        step = math.dist(a, b) / n
        for i in range(n):
            t = (i + 0.5) / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            d = to_segment(p, *bar) - (gb.AGG_MM + gb.VICTIM_MM) / 2 + slack
            d = max(d, 0.0)
            if d <= 3 * H_BOARD:
                raw += step
                effective += step * weight(d)
    return raw, effective


def shoelace(polygon: list[gb.Point]) -> float:
    """Return the area (mm2) of a polygon from its corners."""
    pairs = zip(polygon, polygon[1:] + polygon[:1])
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairs)) / 2


# lambda / 20 at the top of the Wi-Fi band, 2.484 GHz, in an effective permittivity of
# (4.5 + 1) / 2 (half the field in FR4, half in air): 3.64 mm
RF_LIMIT = 299792458.0 / 2.484e9 / math.sqrt((4.5 + 1) / 2) * 1e3 / 20
LATTICE_WORST = gb.PITCH / math.sqrt(
    2
)  # a complete lattice's worst point: a cell's centre


# --- the specs of the control project -------------------------------------------------

CENTRES_MM = (gb.AGG_MM + gb.VICTIM_MM) / 2  # SENSE and PWR at their closest approach
BUDGET_V = grover(LMAX_MM, CENTRES_MM) * DI_DT  # the noise of LMAX_MM, by construction


def reserved_row(name: str, layer: str, box: gb.Box) -> str:
    """Return a row of RESERVED_COPPER: a box where only ground may have copper."""
    x0, y0, x1, y1 = box
    return f'    ("{name}", "{layer}", {x0!r}, {y0!r}, {x1!r}, {y1!r}, {{"/GND"}}),\n'


def segment_text(name: str, net: str, terminals: str, heating: bool = True) -> str:
    """Return a dict of COPPER_SEGMENTS, ``terminals`` being the text of its list."""
    return (
        f'    dict(name="{name}", net="{net}", heating={heating},\n'
        f"         terminals=[{terminals}]),\n"
    )


NEAR, FAR = LOAD_SHARES["near"], LOAD_SHARES["far"]
FILTERS = (
    '    "filters": {"SENSE": {"t_edge": '
    f'{T_EDGE!r}, "pin_ref": "U1", "max_mm": {CAP_MAX_MM!r}}}}},\n'
)
BUDGETS = (
    f"COPPER_BUDGETS = dict(i_cont={I_CONT!r}, i_peak={I_PEAK!r},\n"
    f"                      segment_drop_max_v={DROP_MAX_V!r}, "
    f"max_rise_c={MAX_RISE_C!r})\n"
)
REGULATION_SPEC = (
    "SUPPLY_REGULATION = dict(supply='vin_path', ground='rtn_path',\n"
    f"                         label='the far load', nominal_v={NOMINAL_V!r},\n"
    f"                         max_fraction={MAX_FRACTION!r})\n"
)
SPECS = (
    '"""Specs of the copper-group test board."""\n'
    "from __future__ import annotations\n\n"
    "RESERVED_COPPER = [\n"
    + reserved_row("return_strip", "bottom", gb.STRIP)
    + reserved_row("rf_keepout", "top", gb.KEEPOUT)
    + "]\n"
    "COUPLING = {\n"
    f'    "classes": {{"sense": ({BUDGET_V!r}, 1.0e4, {DI_DT!r})}},\n'
    '    "sensitive": {"sense": ["SENSE"]},\n'
    '    "aggressors": {"supply": {"nets": ["PWR"], "kind": "current"}},\n'
    + FILTERS
    + "}\n"
    "SENSE_LOOPS = [\n"
    '    dict(name="shunt", nets=("SA", "SB"), from_ref="R1", to_ref="R2",\n'
    f'         to_pads=("1", "2"), max_mm2={LOOP_MAX_MM2!r}),\n'
    "]\n"
    "COPPER_SEGMENTS = [\n"
    + segment_text(
        "vin_path",
        "VIN",
        f'("TP2", 1, -{NEAR!r}, "load"), ("TP3", 1, -{FAR!r}, "load"), '
        '("TP1", 1, 1.0, "source")',
    )
    + segment_text(
        "rtn_path",
        "RTN",
        f'("TP5", 1, {NEAR!r}, "load"), ("TP6", 1, {FAR!r}, "load"), '
        '("TP4", 1, -1.0, "source")',
        heating=False,
    )
    + "]\n"
    + BUDGETS
    + REGULATION_SPEC
)


# --- projects: the control, and the control with one thing changed --------------------


@dataclass(frozen=True)
class Variant:
    """The control project with some changes, and nothing else changed.

    ``board`` are keyword arguments of ``check_group_boards.copper_board``; ``stackup``
    and ``stitch`` are keys of those tables of pcbkit.toml; ``specs`` are edits of
    specs.py as (text to find, text to put there).
    """

    board: dict[str, Any] = field(default_factory=dict)
    stackup: dict[str, float] | None = None
    stitch: dict[str, float] | None = None
    specs: tuple[tuple[str, str], ...] = ()


def build(root: Path, variant: Variant) -> gb.CopperBoard:
    """Write the project of ``variant`` into ``root`` and return its board."""
    board = gb.copper_board(root / "kicad", "board", **variant.board)
    write_toml(root, "board", ["copper"], variant.stackup, variant.stitch)
    (root / "specs.py").write_text(SPECS, encoding="utf-8")
    for old, new in variant.specs:
        replace_once(root / "specs.py", old, new)
    write_netlist(root, board.toy)
    return board


def build_variant(root: Path, variant: Variant | None) -> gb.CopperBoard:
    """Build a variant (the control when None) into ``root``; return its board."""
    return build(root, variant or Variant())


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Lab:
    """Return the lab: the control and the plants are built and run on first use."""
    return Lab(tmp_path_factory.mktemp("copper_group"), build_variant)


@pytest.fixture(scope="module")
def control(lab: Lab) -> Result:
    """Return the control project, run: every check of the group on a clean board."""
    return lab.result("control")


def at(result: Result, name: str) -> dict[str, Any]:
    """Return what the one check called ``name`` recorded."""
    return result.run.numbers(name)


# --- the control ----------------------------------------------------------------------


def test_the_control_project_passes_every_copper_check(control: Result) -> None:
    """Run the twelve checks of the group on the clean board, and pass them all."""
    names = {key.split("::")[1] for key in control.run.checks}
    assert names == CONTROL
    assert control.failed() == set(), (control.run.outcomes(), control.run.tail())
    assert control.run.done.returncode == 0, control.run.tail()
    assert {e["group"] for e in control.run.checks.values()} == {"copper"}
    assert all(key.startswith(MODULE) for key in control.run.checks)
    assert control.run.results["copper_mm"] == OZ


def test_the_control_board_agrees_with_its_netlist(control: Result) -> None:
    """Put every pad of the board on the net its netlist names: one plan, two files."""
    gb.assert_matches_netlist(control.board.pcb, control.board.parts)


def test_the_control_board_has_no_clearance_or_short_to_the_stitching_vias(
    control: Result, tmp_path: Path
) -> None:
    """Pass KiCad's own DRC on the control: the copper stays clear of the 96 vias.

    The lattice is only believable as a ground stitching if no other copper had to
    push a via aside. The aggressor bar goes nowhere, so one dangling track is all
    KiCad finds; the pours, holes and tracks are clear of each other.
    """
    report = cli.drc(control.board.pcb, tmp_path / "drc.rpt", severity_all=True).report
    assert len(control.board.vias) == 12 * 8
    errors = [v for v in report.violations if v.severity == "error"]
    assert errors == []
    assert set(report.categories) <= {"track_dangling"}


# --- test_reserved_copper_stays_clear -------------------------------------------------


def test_a_foreign_track_across_the_return_strip_fails_that_region(
    lab: Lab, control: Result
) -> None:
    """Fail the bottom strip's region for the SIG track in it; the other stays clean."""
    strip, keepout = "[return_strip]", "[rf_keepout]"
    assert at(control, strip) == {"return_strip": []}
    assert at(control, keepout) == {"rf_keepout": []}
    result = lab.result("intruder_bottom", Variant(board={"intruder": "bottom"}))
    result.planted("test_reserved_copper_stays_clear[return_strip]")
    assert "track /SIG" in result.run.message("[return_strip]")
    ((what, area),) = at(result, strip)["return_strip"]
    assert what == "track /SIG"
    # the bar of the track is inside the box with both of its round ends
    length = math.dist(*gb.INTRUDERS["bottom"])
    assert area == pytest.approx(0.25 * length + math.pi * 0.125**2, abs=0.02)
    assert at(result, keepout) == {"rf_keepout": []}


def test_a_foreign_track_across_the_keep_out_fails_that_region(lab: Lab) -> None:
    """Fail the top keep-out's region for the SIG track in it; the strip stays clean."""
    result = lab.result("intruder_top", Variant(board={"intruder": "top"}))
    result.planted("test_reserved_copper_stays_clear[rf_keepout]")
    ((what, area),) = at(result, "[rf_keepout]")["rf_keepout"]
    assert what == "track /SIG"
    length = math.dist(*gb.INTRUDERS["top"])
    assert area == pytest.approx(0.25 * length + math.pi * 0.125**2, abs=0.02)
    assert at(result, "[return_strip]") == {"return_strip": []}


# --- test_stitching_basis and test_stitching_density ----------------------------------


PATCHES = "patches_beyond_lambda20 (extent, max dist, centre)"


def test_the_control_stitching_meets_the_rf_limit_with_a_five_millimetre_lattice(
    control: Result,
) -> None:
    """Record lambda / 20 = 3.64 mm, a lattice worst point of 3.54 mm and no patch."""
    numbers = at(control, "test_stitching_density")
    assert numbers["lambda_mm"] == pytest.approx(RF_LIMIT * 20, abs=0.05)
    assert numbers["rf_limit_mm"] == pytest.approx(RF_LIMIT, abs=0.005)
    assert numbers["grid_worst_mm"] == pytest.approx(LATTICE_WORST, abs=0.005)
    assert LATTICE_WORST < RF_LIMIT < 2 * LATTICE_WORST
    # the worst point of the complete lattice is the centre of a cell, pitch / sqrt(2)
    assert numbers["max_dist_to_gnd_via_mm"] == pytest.approx(LATTICE_WORST, abs=0.005)
    assert numbers["p99_dist_to_gnd_via_mm"] <= LATTICE_WORST
    assert numbers[PATCHES] == []
    # any "worst location" it lists is a cell's centre, only just at the limit
    assert all(
        d == pytest.approx(LATTICE_WORST, abs=0.005)
        for _, d in numbers["worst_locations_layout_mm"]
    )


def test_leaving_the_vias_out_of_a_block_fails_the_density_check(lab: Lab) -> None:
    """Fail on an island of ground two pitches from any via, naming where it is."""
    result = lab.result("island", Variant(board={"island": True}))
    result.planted("test_stitching_density")
    assert len(result.board.vias) == 12 * 8 - 9
    numbers = at(result, "test_stitching_density")
    # the block's centre is two pitches from the first via on each side of the block
    assert numbers["max_dist_to_gnd_via_mm"] == pytest.approx(2 * gb.PITCH, abs=0.005)
    ((place, farthest), *_) = numbers["worst_locations_layout_mm"]
    assert place == pytest.approx(gb.ISLAND_CENTRE, abs=0.05)
    assert farthest == pytest.approx(2 * gb.PITCH, abs=0.005)
    patches = numbers[PATCHES]
    assert patches[0][1] == pytest.approx(2 * gb.PITCH, abs=0.01)
    assert patches[0][0] > RF_LIMIT  # a patch wider than lambda / 20 is what fails
    assert numbers["p99_dist_to_gnd_via_mm"] > LATTICE_WORST
    message = result.run.message("test_stitching_density")
    assert f"unstitched patches wider than {RF_LIMIT:.2f} mm" in message
    assert f"max {2 * gb.PITCH:.2f} mm" in message


def test_one_missing_via_fails_the_density_check_by_its_patch_alone(lab: Lab) -> None:
    """Fail the one via left out: a patch wider than lambda / 20, and nothing else."""
    # a pitch of 5.1 mm is still inside the RF limit, and lifts the limit on the 99th
    # percentile to 3.61 mm: the lattice with its one gap stays under it
    variant = Variant(board={"gap": True}, stitch={"pitch_mm": 5.1})
    result = lab.result("gap", variant)
    result.planted("test_stitching_density")
    assert len(result.board.vias) == 12 * 8 - 1
    numbers = at(result, "test_stitching_density")
    # the gap is a pitch from the vias round it, and nothing else is as far from one
    assert numbers["max_dist_to_gnd_via_mm"] == pytest.approx(gb.PITCH, abs=0.005)
    assert numbers["worst_locations_layout_mm"] == [[list(gb.LONE_VIA), gb.PITCH]]
    assert numbers["p99_dist_to_gnd_via_mm"] <= 5.1 / math.sqrt(2)
    ((extent, farthest, centre),) = numbers[PATCHES]
    assert extent > RF_LIMIT and farthest == pytest.approx(gb.PITCH, abs=0.01)
    assert centre == pytest.approx(gb.LONE_VIA, abs=0.1)


def test_a_lattice_coarser_than_the_pitch_claimed_fails_the_density_check(
    lab: Lab,
) -> None:
    """Fail a 5 mm lattice against [stitch] pitch_mm = 4: the 99th percentile alone."""
    result = lab.result("claims_4mm", Variant(stitch={"pitch_mm": 4.0}))
    result.planted("test_stitching_density")
    numbers = at(result, "test_stitching_density")
    assert numbers[PATCHES] == []  # no patch is over lambda / 20
    assert numbers["max_dist_to_gnd_via_mm"] <= RF_LIMIT
    claimed = 4.0 / math.sqrt(2)
    assert claimed < numbers["p99_dist_to_gnd_via_mm"] <= LATTICE_WORST
    message = result.run.message("test_stitching_density")
    assert f"(limit {claimed:.2f})" in message


def test_a_stitch_pitch_too_coarse_for_the_rf_limit_fails_the_basis_check(
    lab: Lab,
) -> None:
    """Fail a 6 mm pitch: its worst point, 4.24 mm, is over lambda / 20 = 3.64 mm."""
    result = lab.result("pitch6", Variant(stitch={"pitch_mm": 6.0}))
    result.planted("test_stitching_basis")
    message = result.run.message("test_stitching_basis")
    assert (
        f"a 6 mm stitching grid leaves points {6 / math.sqrt(2):.2f} mm from a via"
        in message
    )
    assert f"over the {RF_LIMIT:.2f} mm RF limit" in message
    assert "lower [stitch] pitch_mm in pcbkit.toml" in message


def test_the_pitch_that_passes_is_the_rf_limit_times_root_two(lab: Lab) -> None:
    """Pass a pitch just under 5.15 mm and fail one just over it, 3.64 mm * sqrt(2)."""
    limit = RF_LIMIT * math.sqrt(2)
    assert limit == pytest.approx(5.146, abs=0.001)
    under = lab.result("pitch_under", Variant(stitch={"pitch_mm": 5.14}))
    over = lab.result("pitch_over", Variant(stitch={"pitch_mm": 5.16}))
    assert 5.14 < limit < 5.16
    assert "test_stitching_basis" not in under.failed()
    assert "test_stitching_basis" in over.failed()


def test_a_lower_frequency_in_the_specs_allows_the_coarse_pitch(lab: Lab) -> None:
    """Pass the 6 mm pitch when STITCH_BASIS gives 915 MHz: lambda / 20 is 9.9 mm."""
    low_band = "\nSTITCH_BASIS = {'f_hz': 915e6, 'er_fr4': 4.5}\n"
    edit = ("COPPER_BUDGETS = ", f"{low_band}COPPER_BUDGETS = ")
    result = lab.result("pitch6_915", Variant(stitch={"pitch_mm": 6.0}, specs=(edit,)))
    limit = 299792458.0 / 915e6 / math.sqrt((4.5 + 1) / 2) * 1e3 / 20
    assert 6 / math.sqrt(2) < limit
    assert "test_stitching_basis" not in result.failed()
    assert at(result, "test_stitching_density")["rf_limit_mm"] == pytest.approx(
        limit, abs=0.005
    )


# --- test_sensitive_nets_exist --------------------------------------------------------


def test_a_sensitive_net_renamed_away_fails_the_existence_check(
    lab: Lab, control: Result
) -> None:
    """Fail when SENSE has no tracks, while the coupling check passes it for nothing."""
    result = lab.result("renamed", Variant(board={"sensitive": "SENSE_B"}))
    # SENSE is gone from the netlist too, so the filter's capacitor is not found either
    result.planted(
        "test_sensitive_nets_exist",
        "test_filter_caps_sit_by_the_adc_pin[/SENSE]",
    )
    assert "sensitive nets with no tracks (renamed?): ['/SENSE']" in result.run.message(
        "test_sensitive_nets_exist"
    )
    # the run beside the aggressor passes, having found no track of SENSE to look at
    assert result.run.outcome("test_parallel_length[/SENSE]") == "passed"
    assert "supply" not in at(result, "test_parallel_length[/SENSE]")
    assert "supply" in at(control, "test_parallel_length[/SENSE]")


# --- test_lmax_basis ------------------------------------------------------------------


def test_the_lmax_basis_passes_and_matches_the_worked_example(control: Result) -> None:
    """Pass: 10 mm at 4.1 mm centres is 1.9 nH, from Grover's formula written out here.

    There is nothing in a project to get wrong in this check, so it is shown to pass,
    and the number it guards is worked out again: the bracket is asinh(2.44) - 1.081
    + 0.41 = 0.954, and mu0 / 2 pi * 10 mm * 0.954 is 1.91 nH.
    """
    assert control.run.outcome("test_lmax_basis") == "passed"
    assert control.run.entry("test_lmax_basis")["numbers"] == {}
    bracket = math.asinh(10 / 4.1) - math.sqrt(1 + (4.1 / 10) ** 2) + 4.1 / 10
    assert bracket == pytest.approx(0.954, abs=0.001)
    assert grover(10, 4.1) == pytest.approx(2e-7 * 0.01 * bracket)
    assert grover(10, 4.1) == pytest.approx(1.91e-9, abs=0.01e-9)
    assert 1.7e-9 < grover(10, 4.1) < 2.1e-9  # the window the check allows


# --- test_parallel_length -------------------------------------------------------------

REACH = math.sqrt(
    (3 * H_BOARD + (gb.AGG_MM + gb.VICTIM_MM) / 2) ** 2 - (gb.AGG_Y - gb.VICTIM_Y) ** 2
)  # along the victim, past an end of the bar, in 3 h


def run_numbers(result: Result) -> dict[str, Any]:
    """Return what the parallel-length check recorded for SENSE beside PWR."""
    return at(result, "test_parallel_length[/SENSE]")


def assert_coupling(result: Result) -> None:
    """Assert SENSE's recorded run beside PWR is the geometry's, to the raster's slack.

    The 3H rule weights each bit of SENSE by its edge-to-edge distance to PWR; the
    distances are walked here along the path that was built, once a little nearer and
    once a little farther than they are, since the raster puts each edge within a step
    or two of where it is.
    """
    board = result.board
    entry = run_numbers(result)["supply"]
    raw_least, eff_least = coupled(board.victim, board.aggressor, 2 * STEP)
    raw_most, eff_most = coupled(board.victim, board.aggressor, -2 * STEP)
    # the check records two decimals
    assert raw_least - 0.005 <= entry["raw_mm"] <= raw_most + 0.005
    assert eff_least - 0.005 <= entry["effective_mm"] <= eff_most + 0.005
    (x0, y0), (x1, _) = board.aggressor
    start, end = board.victim[0][0], board.victim[-1][0]
    # the run is the part of SENSE within 3 h of the bar: the bar and a reach each side
    box = [max(start, x0 - REACH), board.victim[0][1], min(end, x1 + REACH), y0]
    assert entry["bbox_layout_mm"][0] == pytest.approx(box[0], abs=0.11)
    assert entry["bbox_layout_mm"][2] == pytest.approx(box[2], abs=0.11)
    assert entry["bbox_layout_mm"][1] == pytest.approx(gb.VICTIM_Y, abs=0.11)


def test_the_control_run_is_inside_its_limit_and_the_filter_is_credited(
    control: Result,
) -> None:
    """Pass a 3 mm bar beside SENSE: 6.9 mm effective against 12 mm, uncredited."""
    numbers = run_numbers(control)
    assert numbers["D_near_mm"] == pytest.approx(3 * H_BOARD)
    assert_coupling(control)
    _, effective_most = coupled(
        control.board.victim, control.board.aggressor, -2 * STEP
    )
    assert effective_most < LMAX_MM  # it would pass with no filter at all
    # the filter on SENSE: R10 (10k) and C1 (100n) make tau = 1 ms, and an edge of
    # 10 us passes about a hundredth of its peak
    tau = FILTER_R * FILTER_C
    assert numbers["filter"]["tau_ms"] == pytest.approx(tau * 1e3)
    assert numbers["filter"]["attenuation"] == pytest.approx(T_EDGE / tau)
    credit = tau / T_EDGE
    credited = length_at_noise(BUDGET_V * credit, CENTRES_MM)
    assert numbers["supply"]["lmax_mm"] == pytest.approx(credited, rel=0.001)
    assert credited > 20 * LMAX_MM


def test_a_long_run_beside_the_aggressor_fails_when_no_filter_is_claimed(
    lab: Lab,
) -> None:
    """Fail SENSE beside 30 mm of PWR: 32 mm effective against Lmax = 12 mm."""
    edit = (FILTERS, "")
    variant = Variant(board={"run_mm": 30.0}, specs=(edit,))
    result = lab.result("long_run", variant)
    result.planted("test_parallel_length[/SENSE]")
    entry = run_numbers(result)["supply"]
    assert entry["lmax_mm"] == pytest.approx(LMAX_MM, rel=1e-6)
    assert_coupling(result)
    assert entry["effective_mm"] > 2 * LMAX_MM
    message = result.run.message("test_parallel_length[/SENSE]")
    assert "/SENSE runs too long beside aggressor copper" in message
    assert "'supply'" in message and "12.0" in message
    # with no filter claimed there is no capacitor to place, so that check is skipped
    skipped = [
        k
        for k, v in result.run.checks.items()
        if "test_filter_caps" in k and v["outcome"] == "skipped"
    ]
    assert len(skipped) == 1


def test_the_filter_on_the_net_saves_the_same_long_run(lab: Lab) -> None:
    """Pass the 30 mm run when R10 and C1 filter SENSE: Lmax grows a hundredfold."""
    result = lab.result("long_run_filtered", Variant(board={"run_mm": 30.0}))
    assert result.failed() == set(), result.run.outcomes()
    numbers = run_numbers(result)
    assert_coupling(result)
    effective = numbers["supply"]["effective_mm"]
    assert effective > 2 * LMAX_MM  # a run that fails without the filter
    tau = FILTER_R * FILTER_C
    assert numbers["filter"]["attenuation"] == pytest.approx(T_EDGE / tau)
    credited = length_at_noise(BUDGET_V * tau / T_EDGE, CENTRES_MM)
    assert numbers["supply"]["lmax_mm"] == pytest.approx(credited, rel=0.001)
    assert effective < credited


def test_taking_the_capacitor_away_takes_the_credit_with_it(lab: Lab) -> None:
    """Fail the 30 mm run once C1 is out of the netlist: tau is 0 and Lmax is 12 mm."""
    result = lab.result(
        "long_run_no_cap", Variant(board={"run_mm": 30.0, "with_cap": False})
    )
    result.planted(
        "test_parallel_length[/SENSE]",
        "test_filter_caps_sit_by_the_adc_pin[/SENSE]",
    )
    numbers = run_numbers(result)
    assert numbers["filter"] == {"tau_ms": 0.0, "attenuation": 1.0}
    assert numbers["supply"]["lmax_mm"] == pytest.approx(LMAX_MM, rel=1e-6)
    assert "no filter capacitor on SENSE" in result.run.message(
        "test_filter_caps_sit_by_the_adc_pin[/SENSE]"
    )


# --- test_filter_caps_sit_by_the_adc_pin ----------------------------------------------


def test_a_filter_capacitor_far_from_its_pin_fails(lab: Lab, control: Result) -> None:
    """Fail C1 12 mm from U1's pin 1 against a 5 mm limit; pass it at 2 mm."""
    name = "test_filter_caps_sit_by_the_adc_pin[/SENSE]"
    nearer = at(control, name)["filter cap to ADC pin mm"]
    assert nearer == pytest.approx(
        math.dist(control.board.cap_pad, control.board.adc_pin)
    )
    assert nearer == pytest.approx(2.0, abs=0.01) and nearer <= CAP_MAX_MM
    result = lab.result("cap_far", Variant(board={"cap_mm": 12.0}))
    result.planted(name)
    farther = at(result, name)["filter cap to ADC pin mm"]
    assert farther == pytest.approx(
        math.dist(result.board.cap_pad, result.board.adc_pin)
    )
    assert farther == pytest.approx(12.0, abs=0.01)
    assert f"filter cap {farther:.1f} mm from the ADC pin (limit {CAP_MAX_MM:.0f})" in (
        result.run.message(name)
    )


# --- test_sense_pair_loop_area --------------------------------------------------------


def test_a_wide_sense_loop_fails_where_a_tight_pair_passes(
    lab: Lab, control: Result
) -> None:
    """Fail SB taken the long way round: 116 mm2 enclosed against 60; the pair, 33."""
    name = "test_sense_pair_loop_area[shunt]"
    tight = at(control, name)["sense loop"]
    pair = shoelace(control.board.pair_loop)
    assert pair == pytest.approx(1.65 * 20.0)  # two pads 1.65 mm apart, 20 mm along
    assert tight["area_mm2"] == pytest.approx(pair, abs=0.05)
    assert tight["sense_p_mm"] == pytest.approx(20.0, abs=0.05)
    assert tight["sense_n_mm"] == pytest.approx(20.0, abs=0.05)
    assert tight["limit_mm2"] == LOOP_MAX_MM2
    result = lab.result("wide_loop", Variant(board={"wide_loop": True}))
    result.planted(name)
    wide = at(result, name)["sense loop"]
    area = shoelace(result.board.pair_loop)
    assert area == pytest.approx(20.0 * (gb.WIDE_Y - gb.SENSE_Y + 0.825))
    assert wide["area_mm2"] == pytest.approx(area, abs=0.05)
    assert area > 1.5 * LOOP_MAX_MM2 > 1.5 * pair
    assert wide["sense_n_mm"] == pytest.approx(
        2 * (gb.WIDE_Y - gb.SENSE_Y - 0.825) + 20.0, abs=0.05
    )
    message = result.run.message(name)
    assert "shunt loop encloses" in message and "(limit 60)" in message
    assert "route SA/SB as a pair" in message


# --- test_copper_segment --------------------------------------------------------------

THIN_MM = 0.5  # the far feed of a plant, from the near load to the far one
BARE_MM = gb.LOAD_X["near"] - gb.LOAD_X["source"] - gb.PAD_MM  # 22 mm between pad edges


def drop_band(amps: float, far_mm: float, copper_mm: float = OZ) -> tuple[float, float]:
    """Return the least and most (V) the farthest load can read below the source.

    The current of every load goes along the near feed (3 mm wide), and only the far
    load's share of it on to the far one, ``far_mm`` wide, so the farthest load sits
    ``amps * (R_near + share * R_far)`` from the source, each resistance in its band.
    """
    near_least, near_most = resistance_band(gb.FEED_MM, BARE_MM, copper_mm)
    far_least, far_most = resistance_band(far_mm, BARE_MM, copper_mm)
    share = LOAD_SHARES["far"]
    return amps * (near_least + share * far_least), amps * (
        near_most + share * far_most
    )


def segment(result: Result, name: str) -> dict[str, Any]:
    """Return what the check of the segment ``name`` recorded under that name."""
    return at(result, f"test_copper_segment[{name}]")[name]


def test_the_control_segments_carry_their_current_with_a_modest_rise_and_drop(
    control: Result,
) -> None:
    """Pass both segments: the drop is rho L / (w t), the rise the 3 mm track's."""
    low, high = drop_band(I_PEAK, gb.FEED_MM)
    assert high < DROP_MAX_V  # the budget is not met by luck of the raster
    for name in ("vin_path", "rtn_path"):
        entry = segment(control, name)
        assert low * 1e3 - 0.05 <= entry["drop_mV_at_8A"] <= high * 1e3 + 0.05, name
        assert entry["drop_mV_at_4A"] * 2 == pytest.approx(
            entry["drop_mV_at_8A"], abs=0.11
        )
    vin = segment(control, "vin_path")
    # the narrowest the current can be is the 2 mm of the pad it enters by, and the
    # widest the 3 mm track filled to the cell: 3.2 mm
    assert 2.0 <= vin["narrowest_equiv_width_mm"] <= 3.2 + 0.005
    assert (
        ipc_rise(I_CONT, 3.2) - 0.05
        <= vin["ipc2221_rise_C_at_4A"]
        <= ipc_rise(I_CONT, 2.0) + 0.05
    )
    assert ipc_rise(I_CONT, 2.0) < MAX_RISE_C
    # the return is judged on its drop alone: it has no rise
    rtn = segment(control, "rtn_path")
    assert rtn["narrowest_equiv_width_mm"] is None
    assert rtn["ipc2221_rise_C_at_4A"] is None


def test_a_narrow_feed_to_the_far_load_fails_the_rise_at_one_ounce(
    lab: Lab, control: Result
) -> None:
    """Fail VIN's 0.5 mm far feed: 2.4 A of IPC-2221 heat in 0.5 mm of 1 oz copper."""
    result = lab.result("thin_feed", Variant(board={"far_feed_mm": THIN_MM}))
    name = "test_copper_segment[vin_path]"
    # the far feed also drops 115 mV, so the regulation of the far load fails with it
    result.planted(name, "test_load_supply_regulation")
    entry = segment(result, "vin_path")
    share = LOAD_SHARES["far"]
    # the 99.5th percentile of current per width is the thin feed's, which carries
    # only 60 % of the test current in 0.5 to 0.7 mm of copper: the equivalent width
    # at the whole current is that over the share
    narrow, wide = THIN_MM / share, (THIN_MM + 2 * RES) / share
    assert narrow - 0.005 <= entry["narrowest_equiv_width_mm"] <= wide + 0.005
    assert (
        ipc_rise(I_CONT, wide) - 0.05
        <= entry["ipc2221_rise_C_at_4A"]
        <= ipc_rise(I_CONT, narrow) + 0.05
    )
    assert (
        ipc_rise(I_CONT, wide) > MAX_RISE_C
    )  # over budget at any width the raster reads
    assert (
        f"vin_path: narrowest section ~{entry['narrowest_equiv_width_mm']:.1f} mm"
        in (result.run.message(name))
    )
    assert "C rise at 4 A" in result.run.message(name)
    # the return, built the same way as before, is not affected
    assert result.run.outcome("test_copper_segment[rtn_path]") == "passed"
    assert segment(result, "rtn_path")["drop_mV_at_8A"] == pytest.approx(
        segment(control, "rtn_path")["drop_mV_at_8A"], abs=0.05
    )


def test_two_ounce_copper_passes_the_feed_that_fails_at_one_ounce(lab: Lab) -> None:
    """Pass the 0.5 mm feed at [stackup] copper_mm = 0.070: a threefold lower rise."""
    thin = lab.result("thin_feed", Variant(board={"far_feed_mm": THIN_MM}))
    two_oz = lab.result(
        "thin_feed_2oz",
        Variant(
            board={"far_feed_mm": THIN_MM, "copper_mm": 0.07},
            stackup={"copper_mm": 0.07},
        ),
    )
    assert two_oz.failed() == set(), two_oz.run.outcomes()
    assert two_oz.run.results["copper_mm"] == 0.07
    one, two = segment(thin, "vin_path"), segment(two_oz, "vin_path")
    # the same copper shape carries the same current per width at either weight, and
    # IPC-2221 has the rise go as A^(-0.725 / 0.44): doubling the copper divides it by
    # 3.13
    assert two["narrowest_equiv_width_mm"] == one["narrowest_equiv_width_mm"]
    factor = 2 ** (0.725 / 0.44)
    assert factor == pytest.approx(3.13, abs=0.005)
    assert one["ipc2221_rise_C_at_4A"] / two["ipc2221_rise_C_at_4A"] == pytest.approx(
        factor, rel=0.01
    )
    narrow = THIN_MM / LOAD_SHARES["far"]
    assert two["ipc2221_rise_C_at_4A"] <= ipc_rise(I_CONT, narrow, 0.07) + 0.05
    assert ipc_rise(I_CONT, narrow, 0.07) < MAX_RISE_C  # passes at any width it reads


def test_a_drop_budget_below_what_the_copper_gives_fails_both_segments(
    lab: Lab,
) -> None:
    """Fail a budget of three quarters of the least drop the raster can read at 8 A."""
    low, high = drop_band(I_PEAK, gb.FEED_MM)
    tight = 0.75 * low
    edit = (f"segment_drop_max_v={DROP_MAX_V!r}", f"segment_drop_max_v={tight!r}")
    result = lab.result("tight_drop", Variant(specs=(edit,)))
    result.planted("test_copper_segment[vin_path]", "test_copper_segment[rtn_path]")
    for name in ("vin_path", "rtn_path"):
        drop = segment(result, name)["drop_mV_at_8A"]
        assert low * 1e3 - 0.05 <= drop <= high * 1e3 + 0.05
        assert drop > tight * 1e3
        message = result.run.message(f"test_copper_segment[{name}]")
        assert f"{name} drops {drop:.0f} mV at 8 A" in message


# --- test_load_supply_regulation ------------------------------------------------------

REGULATION = "the far load at 8 A"


def regulation(result: Result) -> dict[str, float]:
    """Return what the regulation check recorded."""
    return at(result, "test_load_supply_regulation")[REGULATION]


def test_the_control_loses_under_a_percent_of_twelve_volts_at_the_far_load(
    control: Result,
) -> None:
    """Pass: sag and lift are each the far load's drop, 90 mV of the 120 allowed."""
    numbers = regulation(control)
    low, high = drop_band(I_PEAK, gb.FEED_MM)
    assert low * 1e3 - 0.05 <= numbers["supply_sag_mV"] <= high * 1e3 + 0.05
    assert low * 1e3 - 0.05 <= numbers["ground_lift_mV"] <= high * 1e3 + 0.05
    assert numbers["total_mV"] == pytest.approx(
        numbers["supply_sag_mV"] + numbers["ground_lift_mV"], abs=0.11
    )
    assert numbers["pct_of_nominal"] == pytest.approx(
        numbers["total_mV"] / (NOMINAL_V * 1e3) * 100, abs=0.01
    )
    assert 2 * high < MAX_FRACTION * NOMINAL_V  # under 120 mV at any reading


def test_a_long_thin_supply_to_the_far_load_fails_the_regulation(
    lab: Lab, control: Result
) -> None:
    """Fail VIN's 0.5 mm far feed: its sag alone is most of the 120 mV allowed."""
    result = lab.result("thin_feed", Variant(board={"far_feed_mm": THIN_MM}))
    result.planted("test_copper_segment[vin_path]", "test_load_supply_regulation")
    numbers = regulation(result)
    sag_least, sag_most = drop_band(I_PEAK, THIN_MM)
    lift_least, lift_most = drop_band(I_PEAK, gb.FEED_MM)
    budget = MAX_FRACTION * NOMINAL_V
    assert sag_least + lift_least > budget  # over budget at any reading of the raster
    assert sag_least * 1e3 - 0.05 <= numbers["supply_sag_mV"] <= sag_most * 1e3 + 0.05
    assert (
        lift_least * 1e3 - 0.05 <= numbers["ground_lift_mV"] <= lift_most * 1e3 + 0.05
    )
    assert numbers["total_mV"] > 1.1 * budget * 1e3
    assert numbers["ground_lift_mV"] == pytest.approx(
        regulation(control)["ground_lift_mV"], abs=0.05
    )  # the return was not touched
    message = result.run.message("test_load_supply_regulation")
    assert "the far load lose" in message and "(>1% of 12 V) at 8 A" in message


def test_a_long_thin_return_from_the_far_load_fails_the_regulation_too(
    lab: Lab, control: Result
) -> None:
    """Fail RTN's 0.5 mm far feed: the ground lifts at the far load instead."""
    result = lab.result("thin_return", Variant(board={"far_return_mm": THIN_MM}))
    # the return's own drop budget (80 mV) is over at 100 mV and more
    result.planted("test_copper_segment[rtn_path]", "test_load_supply_regulation")
    numbers = regulation(result)
    lift_least, lift_most = drop_band(I_PEAK, THIN_MM)
    assert (
        lift_least * 1e3 - 0.05 <= numbers["ground_lift_mV"] <= lift_most * 1e3 + 0.05
    )
    assert numbers["supply_sag_mV"] == pytest.approx(
        regulation(control)["supply_sag_mV"], abs=0.05
    )  # the supply was not touched
    assert lift_least > DROP_MAX_V


def test_without_a_regulation_budget_in_the_specs_the_check_skips_and_says_why(
    lab: Lab,
) -> None:
    """Skip the regulation check when the specs do not name SUPPLY_REGULATION."""
    result = lab.result("no_regulation", Variant(specs=((REGULATION_SPEC, ""),)))
    assert result.failed() == set(), result.run.outcomes()
    entry = result.run.entry("test_load_supply_regulation")
    assert entry["outcome"] == "skipped"
    assert "specs.SUPPLY_REGULATION is not defined" in entry["reason"]


def test_the_roles_say_which_terminals_the_regulation_judges(lab: Lab) -> None:
    """Pass the thin return when its far terminal is not a load, and name a source."""
    far = '("TP6", 1, 0.6, "load")'
    unlabelled = Variant(
        board={"far_return_mm": THIN_MM}, specs=((far, '("TP6", 1, 0.6)'),)
    )
    result = lab.result("thin_return_unlabelled", unlabelled)
    # only the segment check, which has no roles, sees the thin copper: the regulation
    # check judges the terminals it is told are loads, and the near one is all it has
    result.planted("test_copper_segment[rtn_path]")
    near_lift = regulation(result)["ground_lift_mV"]
    least, most = resistance_band(gb.FEED_MM, BARE_MM, OZ)
    assert least * I_PEAK * 1e3 - 0.05 <= near_lift <= most * I_PEAK * 1e3 + 0.05
    # and a segment with no source to measure from cannot be judged at all
    source = '("TP1", 1, 1.0, "source")'
    no_source = lab.result("no_source", Variant(specs=((source, '("TP1", 1, 1.0)'),)))
    no_source.planted("test_load_supply_regulation")
