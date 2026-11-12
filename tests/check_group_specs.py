"""The ``specs.py`` text of the copper and fab check-group test boards, and its numbers.

The two integration modules (``tests/integration/test_check_copper_group.py`` and
``test_check_fab_group.py``) build a real board with pcbnew and run the group on it; the
``specs.py`` they write is made here, so a unit test can hand the same text to
``pcbkit specs`` (``pcbkit.check.tables``) on a machine with no KiCad. Nothing here
imports pcbnew: ``tests.check_group_boards`` imports without it, and the rest is text.

``COPPER_SPECS`` and ``FAB_SPECS`` are the texts. The numbers they are made from are the
plan of each test (budgets, limits, the model), and the integration modules import them
back, so a change here changes the control project of the kicad-marked tests and the
unit test together.
"""

from __future__ import annotations

import math

from tests import check_group_boards as gb

# --- copper: the plan -----------------------------------------------------------------

I_CONT, I_PEAK = 4.0, 8.0  # A: the heating test current, and the current of the drops
MAX_RISE_C = 20.0
DROP_MAX_V = 0.08  # V per segment, at I_PEAK
NOMINAL_V, MAX_FRACTION = 12.0, 0.01  # the far load may lose 1 % of 12 V: 120 mV
LOAD_SHARES = {"near": 0.4, "far": 0.6}  # of the test current, in both segments
LMAX_MM = 12.0  # how far SENSE may run beside PWR at the closest approach, by design
DI_DT = 5.0e6  # A/s of the class "sense"
T_EDGE = 10e-6  # s: the edge the filter on SENSE has to smooth
LOOP_MAX_MM2 = 60.0
CAP_MAX_MM = 5.0


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


# --- copper: the specs of the control project ------------------------------

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
COPPER_SPECS = (
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


# --- fab: the plan and the specs ---------------------------------------------

# generic limits for the test, not any fab house's: the check reads whatever PCBWAY says
LIMITS = {
    "min_annular": 0.15,
    "min_drill": 0.3,
    "min_plated_slot": 0.5,
    "silk_min_height": 0.8,
    "silk_min_stroke": 0.15,
    "min_track_2oz": 0.2,
    "min_space_2oz": 0.2,
}
DECOUPLING_MAX_MM = 3.0
DRILL_MM = 1.0  # the lead of the header, on the datasheet
TR_MAX = 300e-9  # s, for 400 kHz
PIN_C = 10e-12  # F per device
TRACE_C_PER_MM = 0.1e-12
CABLE_M, CABLE_C_PER_M = 0.3, 100e-12

DECOUPLING = f'DECOUPLING = [("U1", 8, ("C1",), {DECOUPLING_MAX_MM!r})]\n'
EXEMPT = 'ASSEMBLY_TEXT_EXEMPT = ("J1",)\n'
POWER = (
    "POWER_FOOTPRINTS = [\n"
    '    dict(ref="Q1", tab_pad=2, tab_net="VBUS", smaller=[(1, 2), (3, 2)]),\n'
    f'    dict(ref="J1", drill_mm={{"1": {DRILL_MM!r}}}),\n'
    "]\n"
)
FAB_SPECS = (
    '"""Specs of the fab-group test board."""\n'
    "from __future__ import annotations\n\n"
    f"PCBWAY = {LIMITS!r}\n"
    'WIRE_PADS = {"J1": ("1", "2")}\n'
    f"{DECOUPLING}"
    f"{POWER}"
    'I2C_BUSES = {"main": ("SDA", "SCL", 400e3, [("DISPLAY", 0x3C, 10)])}\n'
    f"I2C_TR_MAX = {{400e3: {TR_MAX!r}}}\n"
    f"I2C_PIN_C = {PIN_C!r}\n"
    f"TRACE_C_PER_MM = {TRACE_C_PER_MM!r}\n"
    f"I2C_CABLE_LEN = {CABLE_M!r}\n"
    f"I2C_CABLE_C_PER_M = {CABLE_C_PER_M!r}\n"
    f"{EXEMPT}"
)
