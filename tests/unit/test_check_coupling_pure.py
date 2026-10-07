"""Unit tests for the pure parts of pcbkit.check.coupling.

The rest of the module rasterises copper and needs pcbnew. Here: the net-name helper,
the mutual-inductance formula, ``Config`` and its reading from a project's COUPLING
table, the length limits (``lmax_mm``, ``limit``), the verdicts (``violations``), the
RC-filter credit and the loop-area budget. Values are worked out by hand in the tests,
and the netlists are written out in them, with generic parts and nets.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from pcbkit.check.coupling import (
    Aggressor,
    Config,
    class_of,
    filter_credit,
    from_spec,
    limit,
    lmax_mm,
    max_loop_area_mm2,
    mutual_inductance,
    slash,
    violations,
)
from pcbkit.check.netlist import Netlist

# a valid COUPLING table: two classes (budget V, victim ohm, dI/dt A/s), three groups
SPEC: dict[str, Any] = {
    "classes": {"adc": (0.005, 1.0e4, 5.0e6), "fast": (0.01, 100.0, 1.0e6)},
    "sensitive": {"adc": ["SENSE", "/VREF"], "fast": ["CLK"]},
    "aggressors": {
        "motor": {"nets": ["MOTOR_A", "/MOTOR_B"], "kind": "current"},
        "bus": {"nets": ["GND"], "kind": "bar"},
        "node": {"nets": ["SW"], "kind": "switch", "region": [0, 0, 20, 10]},
    },
}


def config(**changes: Any) -> Config:
    """Return a Config with every physical constant spelled out, so hand values hold.

    The switch node swings 8.5 V in 10 ns (8.5e8 V/s) and couples 0.05 pF per mm; the
    victim node holds 10 pF unless ``cn_net`` says otherwise. ``changes`` replace any
    field.
    """
    fields: dict[str, Any] = {
        "classes": {"adc": (0.0425, 1.0e4, 1.0e6), "fast": (0.01, 100.0, 1.0e6)},
        "sensitive": {"adc": ["/SENSE"], "fast": ["/CLK"]},
        "aggressors": {
            "sw": Aggressor(("/SW",), "switch"),
            "cur": Aggressor(("/MOTOR",), "current"),
            "bar": Aggressor(("/GND",), "bar"),
        },
        "h_board": 1.6,
        "c_per_mm": 0.05e-12,
        "dv_dt": 8.5e8,
        "dv_sw": 8.5,
        "cn_default": 10e-12,
        "cn_net": {},
        "bar_min_w": 3.0,
        "bar_w_cap": 7.6,
    }
    fields.update(changes)
    return Config(**fields)


def netlist_of(
    parts: list[tuple[str, str, str]], nets: dict[str, list[str]]
) -> Netlist:
    """Return a Netlist of ``parts`` ``(ref, "Lib:Symbol", value)`` joined by ``nets``.

    ``nets`` maps a net name to the pins on it, each written ``"R1.2"``.
    """
    comps = []
    for ref, kind, value in parts:
        lib, symbol = kind.split(":")
        comps.append(
            f'(comp (ref "{ref}") (value "{value}") '
            f'(libsource (lib "{lib}") (part "{symbol}")))'
        )
    net_items = []
    for code, (name, pins) in enumerate(nets.items(), start=1):
        nodes = " ".join(
            f'(node (ref "{ref}") (pin "{number}"))'
            for ref, number in (pin.split(".") for pin in pins)
        )
        net_items.append(f'(net (code "{code}") (name "/{name}") {nodes})')
    text = (
        '(export (version "E") (design (tool "Eeschema 10.0.6")) '
        f"(components {' '.join(comps)}) (nets {' '.join(net_items)}))"
    )
    return Netlist.from_text(text)


# --- slash ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "written"),
    [
        ("GND", "/GND"),
        ("/GND", "/GND"),
        ("//GND", "/GND"),
        ("SUB/NET", "/SUB/NET"),
        ("/SUB/NET", "/SUB/NET"),
    ],
)
def test_slash_gives_a_net_name_exactly_one_leading_slash(
    given: str, written: str
) -> None:
    """Write a name as KiCad does, adding the slash only if it is missing."""
    assert slash(given) == written


# --- mutual_inductance ---------------------------------------------------------------


def test_mutual_inductance_reproduces_the_worked_example_of_the_module() -> None:
    """Give about 1.9 nH for two 10 mm filaments 4.1 mm apart: 9.5 mV at 5 A/us."""
    m = mutual_inductance(10.0, 4.1)
    assert m == pytest.approx(1.9e-9, rel=0.01)
    assert m * 5e6 == pytest.approx(9.5e-3, rel=0.01)
    # worked by hand from Grover's formula, asinh(x) written as ln(x + sqrt(x^2 + 1))
    x = 10.0 / 4.1
    bracket = math.log(x + math.sqrt(x * x + 1)) - math.sqrt(1 + 0.41**2) + 0.41
    assert m == pytest.approx(2e-7 * 0.010 * bracket, rel=1e-12)
    assert m == pytest.approx(1.907e-9, rel=1e-3)


def test_for_long_filaments_it_approaches_the_wire_pair_formula() -> None:
    """Approach 2e-7 * l * (ln(2 l / d) - 1) henries as l grows far past d."""
    m = mutual_inductance(1000.0, 1.0)  # 1 m of wire, 1 mm apart
    assert m == pytest.approx(2e-7 * 1.0 * (math.log(2000.0) - 1), rel=1e-3)


def test_for_short_filaments_far_apart_it_approaches_l_squared_over_d() -> None:
    """Approach 1e-7 * l^2 / d when l is a hundredth of d (1 mm filaments, 100 mm)."""
    m = mutual_inductance(1.0, 100.0)
    assert m == pytest.approx(1e-7 * (1e-3) ** 2 / 0.1, rel=1e-3)


def test_mutual_inductance_scales_with_the_size_of_the_pair() -> None:
    """Double when the length and the distance both double, as inductance does."""
    assert mutual_inductance(20.0, 8.2) == pytest.approx(
        2 * mutual_inductance(10.0, 4.1), rel=1e-12
    )
    assert mutual_inductance(5.0, 2.05) == pytest.approx(
        0.5 * mutual_inductance(10.0, 4.1), rel=1e-12
    )


def test_mutual_inductance_grows_with_length_and_falls_with_distance() -> None:
    """Rise with each doubling of the length, and fall with each doubling of d."""
    by_length = [
        mutual_inductance(length, 4.1) for length in (2.5, 5.0, 10.0, 20.0, 40.0)
    ]
    assert all(0 < a < b for a, b in zip(by_length, by_length[1:]))
    by_distance = [mutual_inductance(10.0, d) for d in (1.0, 2.0, 4.0, 8.0, 16.0)]
    assert all(a > b > 0 for a, b in zip(by_distance, by_distance[1:]))


# --- Config and from_spec ------------------------------------------------------------


def test_from_spec_reads_a_valid_table() -> None:
    """Read classes, sensitive nets and aggressors; add slashes, tuple the region."""
    cfg = from_spec(SPEC, h_board=2.0)
    assert cfg.classes == SPEC["classes"]
    assert cfg.sensitive == {"adc": ["/SENSE", "/VREF"], "fast": ["/CLK"]}
    assert cfg.aggressors == {
        "motor": Aggressor(("/MOTOR_A", "/MOTOR_B"), "current", None),
        "bus": Aggressor(("/GND",), "bar", None),
        "node": Aggressor(("/SW",), "switch", (0, 0, 20, 10)),
    }
    assert isinstance(cfg.aggressors["node"].region, tuple)


def test_the_board_thickness_comes_from_the_argument_and_sets_the_window() -> None:
    """Take h_board from the argument (1.6 mm if omitted); d_near is three times it."""
    cfg = from_spec(SPEC, h_board=2.0)
    assert cfg.h_board == 2.0
    assert cfg.d_near == 6.0
    default = from_spec(SPEC)
    assert default.h_board == 1.6
    assert default.d_near == pytest.approx(4.8, rel=1e-12)


def test_all_sensitive_lists_every_sensitive_net_in_class_order() -> None:
    """Flatten the classes in the order given, each net with its slash."""
    assert from_spec(SPEC).all_sensitive == ["/SENSE", "/VREF", "/CLK"]


def test_from_spec_takes_constants_and_per_net_capacitance_from_the_table() -> None:
    """Override a constant by naming it, and write ``cn_net`` keys with the slash."""
    table = {
        **SPEC,
        "c_per_mm": 0.1e-12,
        "bar_min_w": 2.0,
        "cn_net": {"SENSE": 5e-12, "/VREF": 7e-12},
        "filters": {"SENSE": {"t_edge": 1e-6, "pin_ref": "U1", "max_mm": 5.0}},
    }
    cfg = from_spec(table)
    assert cfg.c_per_mm == 0.1e-12
    assert cfg.bar_min_w == 2.0
    assert cfg.cn_net == {"/SENSE": 5e-12, "/VREF": 7e-12}
    assert not hasattr(cfg, "filters")  # filters are read by the check, not the Config


def test_a_key_that_is_not_a_config_field_raises_value_error_naming_it() -> None:
    """Catch a typo in the table: ``colour`` is not a known key."""
    with pytest.raises(ValueError) as caught:
        from_spec({**SPEC, "colour": 1})
    message = str(caught.value)
    assert "colour" in message
    assert "classes" in message  # it lists the keys that are known


def test_every_unknown_key_is_named_in_sorted_order() -> None:
    """Name both keys, ``alpha`` then ``zeta``, whatever order the table had them in."""
    with pytest.raises(ValueError) as caught:
        from_spec({**SPEC, "zeta": 1, "alpha": 2})
    assert "['alpha', 'zeta']" in str(caught.value)


def test_the_board_thickness_cannot_be_set_in_the_table() -> None:
    """Refuse ``h_board`` in the table, since the stackup is where it comes from."""
    with pytest.raises(ValueError, match="h_board"):
        from_spec({**SPEC, "h_board": 1.0})


def test_an_aggressor_kind_that_is_not_bar_current_or_switch_raises() -> None:
    """Name the aggressor and the kind, and the kinds that are allowed."""
    table = {**SPEC, "aggressors": {"hum": {"nets": ["A"], "kind": "weird"}}}
    with pytest.raises(ValueError) as caught:
        from_spec(table)
    message = str(caught.value)
    assert "hum" in message
    assert "weird" in message
    assert all(kind in message for kind in ("bar", "current", "switch"))


# --- lmax_mm: switching nodes (capacitive) -------------------------------------------


@pytest.mark.parametrize(
    ("net", "limit_mm"),
    [
        # adc: Z * C' * dV/dt = 1e4 * 0.05e-12 * 8.5e8 = 0.425 V/mm (slow edge)
        #      dV * C' / Cn = 8.5 * 0.05e-12 / 10e-12 = 0.0425 V/mm (charge sharing)
        #      the smaller wins: 0.0425 V budget / 0.0425 V/mm = 1 mm
        ("/SENSE", 1.0),
        # fast: 100 * 0.05e-12 * 8.5e8 = 0.00425 V/mm is the smaller: 0.01 / 0.00425 mm
        ("/CLK", 0.01 / 0.00425),
    ],
)
def test_a_switch_node_limit_is_the_budget_over_the_smaller_of_two_noise_rates(
    net: str, limit_mm: float
) -> None:
    """Divide the budget by min(Z * C' * dV/dt, dV * C' / Cn), per mm of run."""
    assert lmax_mm(config(), net, "switch", 0.25, 0.25) == pytest.approx(
        limit_mm, rel=1e-9
    )


def test_a_switch_node_limit_follows_the_victims_own_capacitance() -> None:
    """Allow 10 mm when the node holds 100 pF: 8.5 * 0.05e-12 / 100e-12 = 4.25 mV/mm."""
    cfg = config(cn_net={"/SENSE": 100e-12})
    assert lmax_mm(cfg, "/SENSE", "switch", 0.25, 0.25) == pytest.approx(10.0, rel=1e-9)
    assert lmax_mm(cfg, "/CLK", "switch", 0.25, 0.25) == pytest.approx(
        0.01 / 0.00425, rel=1e-9
    )  # another net still uses cn_default


def test_a_switch_node_limit_ignores_widths_and_scales_with_the_credit() -> None:
    """Leave widths out of a capacitive limit, and multiply it by the filter credit."""
    cfg = config()
    base = lmax_mm(cfg, "/SENSE", "switch", 0.25, 0.25)
    assert lmax_mm(cfg, "/SENSE", "switch", 1.0, 9.0) == base
    assert lmax_mm(cfg, "/SENSE", "switch", 0.25, 0.25, credit=0.25) == pytest.approx(
        0.25 * base, rel=1e-12
    )


# --- lmax_mm: current-carrying copper (inductive) ------------------------------------


@pytest.mark.parametrize(
    ("length", "w_victim", "w_agg"),
    [
        (10.0, 0.6, 7.6),  # the module's worked example: (0.6 + 7.6) / 2 = 4.1 mm
        (0.05, 0.1, 0.3),  # a very short allowance, at 0.2 mm
        (500.0, 0.5, 1.5),  # a loose budget, at 1.0 mm: far longer than a board
        (2000.0, 0.5, 1.5),
    ],
)
def test_a_current_limit_recovers_the_length_that_produced_the_budget(
    length: float, w_victim: float, w_agg: float
) -> None:
    """Take the budget that a run of ``length`` makes at 5 A/us and give the length."""
    d = (w_victim + w_agg) / 2
    budget = mutual_inductance(length, d) * 5e6
    cfg = config(classes={"adc": (budget, 1.0e4, 5e6)})
    found = lmax_mm(cfg, "/SENSE", "current", w_victim, w_agg)
    assert found == pytest.approx(length, rel=1e-6)


@pytest.mark.parametrize("kind", ["current", "bar"])
@pytest.mark.parametrize("credit", [1.0, 0.5, 0.1])
@pytest.mark.parametrize(("w_victim", "w_agg"), [(0.25, 0.5), (0.6, 7.6), (1.0, 3.0)])
def test_the_length_found_makes_the_induced_noise_equal_the_budget(
    kind: str, credit: float, w_victim: float, w_agg: float
) -> None:
    """Satisfy M(Lmax, d) * dI/dt = budget * credit, with d the centre-to-centre gap."""
    cfg = config(classes={"adc": (0.0425, 1.0e4, 2.5e6), "fast": (0.01, 100.0, 1e6)})
    length = lmax_mm(cfg, "/SENSE", kind, w_victim, w_agg, credit)
    d = (w_victim + w_agg) / 2
    assert mutual_inductance(length, d) * 2.5e6 == pytest.approx(
        0.0425 * credit, rel=1e-9
    )


def test_a_tighter_budget_gives_a_shorter_length() -> None:
    """Shorten the allowed run as the noise budget falls."""
    lengths = [
        lmax_mm(
            config(classes={"adc": (budget, 1e4, 5e6)}), "/SENSE", "current", 0.25, 0.5
        )
        for budget in (0.001, 0.002, 0.005, 0.010, 0.020)
    ]
    assert all(a < b for a, b in zip(lengths, lengths[1:]))


def test_a_faster_current_edge_gives_a_shorter_length() -> None:
    """Shorten the allowed run as dI/dt rises."""
    lengths = [
        lmax_mm(
            config(classes={"adc": (0.01, 1e4, di_dt)}), "/SENSE", "current", 0.25, 0.5
        )
        for di_dt in (1e6, 2e6, 5e6, 1e7)
    ]
    assert all(a > b for a, b in zip(lengths, lengths[1:]))


def test_more_clearance_between_the_traces_allows_a_longer_run() -> None:
    """Lengthen the run as either trace gets wider, up to the aggressor's width cap."""
    cfg = config()
    by_victim = [
        lmax_mm(cfg, "/SENSE", "current", w, 2.0) for w in (0.2, 0.4, 0.8, 1.6)
    ]
    assert all(a < b for a, b in zip(by_victim, by_victim[1:]))
    by_agg = [lmax_mm(cfg, "/SENSE", "current", 0.25, w) for w in (0.5, 1.0, 2.0, 4.0)]
    assert all(a < b for a, b in zip(by_agg, by_agg[1:]))


def test_an_aggressor_wider_than_the_cap_counts_as_the_cap_wide() -> None:
    """Treat a 20 mm pour as the 7.6 mm cap, not as a conductor 20 mm wide."""
    cfg = config()
    capped = lmax_mm(cfg, "/SENSE", "current", 0.25, 7.6)
    assert lmax_mm(cfg, "/SENSE", "current", 0.25, 20.0) == capped
    assert lmax_mm(cfg, "/SENSE", "current", 0.25, 7.0) < capped
    wide_cap = config(bar_w_cap=30.0)
    assert lmax_mm(wide_cap, "/SENSE", "current", 0.25, 20.0) > capped


def test_a_bar_and_a_current_group_share_the_inductive_limit() -> None:
    """Use one formula for both: only a switch node is treated as capacitive."""
    cfg = config()
    assert lmax_mm(cfg, "/SENSE", "bar", 0.3, 4.0) == lmax_mm(
        cfg, "/SENSE", "current", 0.3, 4.0
    )
    assert lmax_mm(cfg, "/SENSE", "bar", 0.3, 4.0) != lmax_mm(
        cfg, "/SENSE", "switch", 0.3, 4.0
    )


# --- class_of and limit --------------------------------------------------------------


def test_class_of_names_the_class_a_sensitive_net_is_listed_under() -> None:
    """Find the class from the net's name with its slash."""
    cfg = from_spec(SPEC)
    assert class_of(cfg, "/SENSE") == "adc"
    assert class_of(cfg, "/VREF") == "adc"
    assert class_of(cfg, "/CLK") == "fast"


def test_limit_applies_the_kind_of_the_group_to_the_widths_of_the_run() -> None:
    """Use the group's kind, the run's victim width and the aggressor's width."""
    cfg = config()
    run = {
        "width": 0.4,
        "width_agg": 9.0,
    }  # 9 mm is past the 7.6 mm cap, so order shows
    assert limit(cfg, "/SENSE", "sw", run) == pytest.approx(1.0, rel=1e-9)
    assert limit(cfg, "/SENSE", "cur", run) == lmax_mm(
        cfg, "/SENSE", "current", 0.4, 9.0
    )
    assert limit(cfg, "/SENSE", "bar", run) == lmax_mm(cfg, "/SENSE", "bar", 0.4, 9.0)
    assert limit(cfg, "/SENSE", "cur", run) != lmax_mm(
        cfg, "/SENSE", "current", 9.0, 0.4
    )


def test_limit_passes_the_filter_credit_on() -> None:
    """Halve a switch node's limit when the credit is 0.5."""
    run = {"width": 0.4, "width_agg": 3.0}
    assert limit(config(), "/SENSE", "sw", run, credit=0.5) == pytest.approx(
        0.5, rel=1e-9
    )


# --- violations ----------------------------------------------------------------------


def entry(length: float, bbox: list[float]) -> dict[str, Any]:
    """Return what ``analyse`` records for a net beside a group."""
    return {
        "length": length,
        "raw": length * 2,
        "width": 0.25,
        "width_agg": 0.25,
        "bbox": bbox,
    }


def test_violations_lists_the_pairs_over_their_limit_and_only_those() -> None:
    """Report /SENSE beside the switch node at 2.0 mm (limit 1.0), not pairs under."""
    cfg = config()
    res = {
        "/SENSE": {
            "sw": entry(2.04, [1.0, 2.0, 3.0, 4.0]),
            "cur": entry(0.01, [0] * 4),
        },
        "/CLK": {"sw": entry(0.5, [0] * 4)},  # limit 2.35 mm: under
    }
    assert violations(cfg, res) == [("/SENSE", "sw", 2.0, 1.0, [1.0, 2.0, 3.0, 4.0])]


def test_violations_rounds_to_a_tenth_and_keeps_the_order_of_the_results() -> None:
    """Print effective length and limit to 0.1 mm, in the order the results had."""
    cfg = config()
    long_run = entry(1000.0, [0.0, 0.0, 9.0, 9.0])
    res = {
        "/CLK": {"sw": entry(3.04, [5.0, 5.0, 6.0, 6.0])},
        "/SENSE": {"cur": long_run, "sw": entry(1.06, [7.0, 7.0, 8.0, 8.0])},
    }
    assert violations(cfg, res) == [
        ("/CLK", "sw", 3.0, 2.4, [5.0, 5.0, 6.0, 6.0]),
        (
            "/SENSE",
            "cur",
            1000.0,
            round(lmax_mm(cfg, "/SENSE", "current", 0.25, 0.25), 1),
            [0.0, 0.0, 9.0, 9.0],
        ),
        ("/SENSE", "sw", 1.1, 1.0, [7.0, 7.0, 8.0, 8.0]),
    ]


def test_a_filter_credit_moves_the_limit_and_so_the_verdict() -> None:
    """Pass 0.8 mm under a 1.0 mm limit, and fail it under a 0.5 mm one."""
    cfg = config()
    res = {"/SENSE": {"sw": entry(0.8, [1.0, 1.0, 2.0, 2.0])}}
    assert violations(cfg, res) == []
    assert violations(cfg, res, credit={"/SENSE": 1.0}) == []
    assert violations(cfg, res, credit={"/SENSE": 0.5}) == [
        ("/SENSE", "sw", 0.8, 0.5, [1.0, 1.0, 2.0, 2.0])
    ]
    assert violations(cfg, res, credit={"/CLK": 0.1}) == []  # another net's credit


def test_violations_of_nothing_is_empty() -> None:
    """Return an empty list for no results, and for a net near no group."""
    assert violations(config(), {}) == []
    assert violations(config(), {"/SENSE": {}}) == []


# --- filter_credit -------------------------------------------------------------------


def filter_netlist(
    extra_caps: list[tuple[str, str, str, str]] | None = None,
) -> Netlist:
    """Return a divider R1 (10k to VIN) and R2 (10k to GND) on SENSE, with C1 100n.

    ``extra_caps`` are more capacitors, each ``(ref, kind, value, other net)`` from
    SENSE to the other net.
    """
    parts = [
        ("R1", "Device:R", "10k"),
        ("R2", "Device:R", "10k"),
        ("C1", "Device:C", "100n"),
    ]
    nets: dict[str, list[str]] = {
        "VIN": ["R1.1"],
        "SENSE": ["R1.2", "R2.1", "C1.1"],
        "GND": ["R2.2", "C1.2"],
    }
    for ref, kind, value, other in extra_caps or []:
        parts.append((ref, kind, value))
        nets["SENSE"].append(f"{ref}.1")
        nets.setdefault(other, []).append(f"{ref}.2")
    return netlist_of(parts, nets)


def test_the_filter_credit_is_the_edge_over_tau_of_the_parallel_resistance() -> None:
    """Take 10k || 10k = 5k, tau = 5k * 100n = 0.5 ms, and 50 us / 0.5 ms = 0.1."""
    attenuation, tau = filter_credit(filter_netlist(), "SENSE", t_edge=50e-6)
    assert tau == pytest.approx(5e-4, rel=1e-12)
    assert attenuation == pytest.approx(0.1, rel=1e-12)


def test_the_net_may_be_named_with_or_without_its_slash() -> None:
    """Read ``/SENSE`` as ``SENSE``."""
    assert filter_credit(filter_netlist(), "/SENSE", 50e-6) == filter_credit(
        filter_netlist(), "SENSE", 50e-6
    )


def test_an_edge_slower_than_tau_earns_no_credit_but_tau_is_still_reported() -> None:
    """Cap the attenuation at 1.0 when the edge is as slow as the filter or slower."""
    for t_edge in (5e-4, 1e-3, 1.0):
        attenuation, tau = filter_credit(filter_netlist(), "SENSE", t_edge)
        assert attenuation == 1.0
        assert tau == pytest.approx(5e-4, rel=1e-12)


def test_capacitors_to_ground_add_up_and_polarised_ones_count() -> None:
    """Sum 100n + 47n + 10u: tau = 5k * 10.147u = 50.735 ms."""
    nl = filter_netlist(
        [
            ("C2", "Device:C", "47n", "GND"),
            ("C3", "Device:C_Polarized", "10u", "GND"),
        ]
    )
    attenuation, tau = filter_credit(nl, "SENSE", t_edge=1e-3)
    assert tau == pytest.approx(5e3 * (100e-9 + 47e-9 + 10e-6), rel=1e-9)
    assert attenuation == pytest.approx(1e-3 / tau, rel=1e-12)


def test_a_smaller_capacitor_earns_less_credit() -> None:
    """Lose the credit as C1 shrinks: 1n gives tau = 5 us, shorter than a 50 us edge."""
    nl = netlist_of(
        [
            ("R1", "Device:R", "10k"),
            ("R2", "Device:R", "10k"),
            ("C1", "Device:C", "1n"),
        ],
        {"VIN": ["R1.1"], "SENSE": ["R1.2", "R2.1", "C1.1"], "GND": ["R2.2", "C1.2"]},
    )
    attenuation, tau = filter_credit(nl, "SENSE", 50e-6)
    assert attenuation == 1.0
    assert tau == pytest.approx(5e-6, rel=1e-12)


def test_a_capacitor_to_another_net_is_not_a_filter() -> None:
    """Count only capacitors from the net to the ground net."""
    nl = netlist_of(
        [("R1", "Device:R", "10k"), ("C1", "Device:C", "100n")],
        {"VIN": ["R1.1"], "SENSE": ["R1.2", "C1.1"], "OTHER": ["C1.2"]},
    )
    assert filter_credit(nl, "SENSE", 50e-6) == (1.0, 0.0)


def test_the_ground_net_is_a_parameter() -> None:
    """Count capacitors to AGND when told AGND is ground, and not those to GND."""
    nl = netlist_of(
        [
            ("R1", "Device:R", "10k"),
            ("C1", "Device:C", "100n"),
            ("C2", "Device:C", "1u"),
        ],
        {
            "VIN": ["R1.1"],
            "SENSE": ["R1.2", "C1.1", "C2.1"],
            "GND": ["C1.2"],
            "AGND": ["C2.2"],
        },
    )
    assert filter_credit(nl, "SENSE", 1e-4)[1] == pytest.approx(1e4 * 100e-9, rel=1e-12)
    assert filter_credit(nl, "SENSE", 1e-4, ground="AGND")[1] == pytest.approx(
        1e4 * 1e-6, rel=1e-12
    )


@pytest.mark.parametrize("net", ["VIN", "GND", "NOPE"])
def test_a_node_with_no_filter_gets_no_credit_and_a_zero_tau(net: str) -> None:
    """Return (1.0, 0.0) with a resistor or a capacitor but not both, or neither."""
    nl = filter_netlist()
    assert filter_credit(nl, net, 50e-6) == (1.0, 0.0)


def test_a_capacitor_with_no_resistor_on_the_net_gets_no_credit() -> None:
    """Return (1.0, 0.0) for a net that has a capacitor to ground but no source."""
    nl = netlist_of([("C1", "Device:C", "100n")], {"SENSE": ["C1.1"], "GND": ["C1.2"]})
    assert filter_credit(nl, "SENSE", 50e-6) == (1.0, 0.0)


# --- max_loop_area_mm2 ---------------------------------------------------------------


def test_the_loop_area_for_one_nanohenry_at_10_mm_is_50_square_millimetres() -> None:
    """Solve M = mu0 * A / (2 pi r) for A: 1e-9 * 2 pi * 0.01 / 4e-7 pi = 5e-5 m^2."""
    assert max_loop_area_mm2(1e-9, 10.0) == pytest.approx(50.0, rel=1e-12)


@pytest.mark.parametrize(("m_h", "r_mm"), [(1e-9, 10.0), (0.5e-9, 8.0), (3e-9, 25.0)])
def test_the_area_gives_back_the_inductance_it_was_computed_from(
    m_h: float, r_mm: float
) -> None:
    """Satisfy M = mu0 * A / (2 pi r) for a small loop at distance r from a current."""
    mu0 = 4e-7 * math.pi
    area_m2 = max_loop_area_mm2(m_h, r_mm) * 1e-6
    assert mu0 * area_m2 / (2 * math.pi * r_mm * 1e-3) == pytest.approx(m_h, rel=1e-12)


def test_the_loop_area_is_proportional_to_the_budget_and_to_the_distance() -> None:
    """Double the area when either the inductance or the distance doubles."""
    base = max_loop_area_mm2(1e-9, 10.0)
    assert max_loop_area_mm2(2e-9, 10.0) == pytest.approx(2 * base, rel=1e-12)
    assert max_loop_area_mm2(1e-9, 20.0) == pytest.approx(2 * base, rel=1e-12)
    assert max_loop_area_mm2(0.0, 10.0) == 0.0
