"""Unit tests for pcbkit.check.dc: zero-ohm and not-fitted resistors, current sources as
drives, and the opt-in ``build(nl, extended=True)`` models.

The netlists are written with pin names, as KiCad 10 writes them, through the generic
toy builder (``tests/check_toy.py``). Expected values are worked out beside each test.
"""

from __future__ import annotations

import pytest

from pcbkit.check.dc import Circuit, build
from pcbkit.check.netlist import Netlist
from tests import check_toy
from tests.check_toy import Toy

REL = 1e-4


def netlist(toy: Toy) -> Netlist:
    """Return the netlist KiCad 10 would write for the toy."""
    return Netlist.from_text(check_toy.netlist_text(toy))


def two_pad(toy: Toy, ref: str, kind: str, value: str, a: str, b: str) -> None:
    """Add a two-pad part with unnamed pins, pad 1 on ``a`` and pad 2 on ``b``."""
    toy.add(ref, value, kind, {"1": (a, "~"), "2": (b, "~")})


def new_symbols() -> Toy:
    """Return one of every symbol ``extended`` adds, plus the FETs ``build`` knows."""
    toy = Toy()
    for ref, kind in (
        ("R1", "Device:R_Small"),
        ("R2", "Device:R_US"),
        ("R3", "Device:R_Small_US"),
    ):
        two_pad(toy, ref, kind, "1k", "A", "B")
    toy.add("D1", "1N4148", "Device:D", {"1": ("K1", "K"), "2": ("A1", "A")})
    toy.add("D2", "D", "Device:D_Small", {"1": ("K2", "K"), "2": ("A2", "A")})
    toy.add("D3", "Z", "Device:D_Zener", {"1": ("K3", "K"), "2": ("A3", "A")})
    toy.add("D4", "TVS", "Device:D_TVS", {"1": ("T1", "A1"), "2": ("T2", "A2")})
    toy.add("D5", "TVS", "Device:D_TVS", {"1": ("K5", "K"), "2": ("A5", "A")})
    toy.add(
        "Q1",
        "N",
        "Device:Q_NMOS_GSD",
        {"1": ("G1", "G"), "2": ("S1", "S"), "3": ("D1N", "D")},
    )
    toy.add(
        "Q2",
        "P",
        "Device:Q_PMOS_DGS",
        {"1": ("D2N", "D"), "2": ("G2", "G"), "3": ("S2", "S")},
    )
    toy.add(
        "Q3",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("G3", "G"), "2": ("S3", "S"), "3": ("D3N", "D")},
    )
    return toy


# --- on by default: zero ohms, not fitted, current sources ---------------------------


def test_a_zero_ohm_resistor_is_one_milliohm_and_solves() -> None:
    """``0`` and ``0R`` used to divide by zero in the solve; now 1 mohm."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "0", "EN_SHARED", "EN")
    check_toy.resistor(toy, "R2", "100k", "EN_SHARED", "GND")
    check_toy.resistor(toy, "R3", "0R", "EN", "X")
    c = build(netlist(toy))
    assert sorted(r for _a, _b, r, _ref in c.res) == [1e-3, 1e-3, 1e5]
    c.fix("GND", 0.0).fix("X", 1.0)
    sol = c.solve()
    assert sol["EN"] == pytest.approx(1.0, rel=1e-6)


@pytest.mark.parametrize("mark", ["DNP", "dnf", "NF", "NP", "NC", "DNI", "NM", "NOPOP"])
def test_a_resistor_marked_not_fitted_is_left_out(mark: str) -> None:
    """A not-fitted mark alone used to raise in ``parse_value``; now no element."""
    toy = Toy()
    check_toy.resistor(toy, "R1", mark, "A", "B")
    check_toy.resistor(toy, "R2", "1k", "A", "B")
    assert [ref for *_x, ref in build(netlist(toy)).res] == ["R2"]


def test_a_value_that_is_not_a_value_still_raises() -> None:
    """Only a not-fitted mark alone is left out; ``10k DNP`` and junk are not."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "banana", "A", "B")
    with pytest.raises(ValueError, match="banana"):
        build(netlist(toy))


def test_a_current_source_is_a_drive() -> None:
    """A net held only by ``inject`` is not floating, nor is one it reaches."""
    c = Circuit()
    c.R("X", "Y", 1000.0, "R1")
    c.inject("X", 1e-3)
    sol = c.solve()
    assert sol.floating("X") is False
    assert sol.floating("Y") is False
    assert Circuit().solve().floating("X") is True


# --- default build: exactly as before ---------------------------------------------


def test_without_extended_the_new_symbols_build_what_they_built_before() -> None:
    """Only the 2N7002, as before: no resistor, no diode, no body diode."""
    c = build(netlist(new_symbols()))
    assert c.res == []
    assert c.diodes == []
    assert [f["ref"] for f in c.fets] == ["Q3"]
    assert c.fets[0] == {
        "kind": "n",
        "g": "G3",
        "d": "D3N",
        "s": "S3",
        "vth": 2.0,
        "ron": 5.0,
        "ref": "Q3",
    }


def test_without_extended_a_part_may_still_be_modelled_twice() -> None:
    """Nothing about duplicate refs changes outside extended mode."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "A", "B")
    c = build(netlist(toy))
    c.R("A", "B", 1000.0, "R1")
    c.fets.append({"ref": "R1", "kind": "n", "g": "A", "d": "B", "s": "B"})
    assert [ref for *_x, ref in c.res] == ["R1", "R1"]


# --- extended -----------------------------------------------------------------------


def test_extended_models_the_other_resistor_symbols() -> None:
    """R_Small, R_US, R_Small_US: pad 1 to pad 2 at their value."""
    c = build(netlist(new_symbols()), extended=True)
    assert [(a, b, r, ref) for a, b, r, ref in c.res if ref.startswith("R")] == [
        ("A", "B", 1000.0, "R1"),
        ("A", "B", 1000.0, "R2"),
        ("A", "B", 1000.0, "R3"),
    ]


def test_extended_models_two_pad_diodes_by_their_a_and_k_pads() -> None:
    """D, D_Small, D_Zener, an A/K D_TVS conduct forward; an A1/A2 TVS is open."""
    c = build(netlist(new_symbols()), extended=True)
    by_ref = {ref: (a, k) for a, k, _vf, _ron, ref in c.diodes}
    assert by_ref["D1"] == ("A1", "K1")
    assert by_ref["D2"] == ("A2", "K2")
    assert by_ref["D3"] == ("A3", "K3")
    assert by_ref["D5"] == ("A5", "K5")
    assert "D4" not in by_ref
    # the bidirectional TVS is held, as an open element, so the checks see it modelled
    assert [(a, b, ref) for a, b, _r, ref in c.res if ref.startswith("D4")] == [
        ("T1", "T2", "D4.open")
    ]


def test_extended_reads_generic_fets_from_their_pin_names_not_their_symbol_names() -> (
    None
):
    """Q_PMOS_DGS here is wired 1=D, 2=G, 3=S, and the names say so."""
    c = build(netlist(new_symbols()), extended=True)
    fets = {f["ref"]: f for f in c.fets}
    assert (fets["Q1"]["kind"], fets["Q1"]["g"], fets["Q1"]["d"], fets["Q1"]["s"]) == (
        "n",
        "G1",
        "D1N",
        "S1",
    )
    assert (fets["Q2"]["kind"], fets["Q2"]["g"], fets["Q2"]["d"], fets["Q2"]["s"]) == (
        "p",
        "G2",
        "D2N",
        "S2",
    )
    # names that disagree with the suffix: the names win
    toy = Toy()
    toy.add(
        "Q9",
        "N",
        "Device:Q_NMOS_GSD",
        {"1": ("DD", "D"), "2": ("GG", "G"), "3": ("SS", "S")},
    )
    q9 = build(netlist(toy), extended=True).fets[0]
    assert (q9["g"], q9["d"], q9["s"]) == ("GG", "DD", "SS")


def test_extended_gives_every_fet_a_body_diode_in_the_right_direction() -> None:
    """N-FET: source to drain. P-FET: drain to source. The 2N7002 gets one too."""
    c = build(netlist(new_symbols()), extended=True)
    body = {ref: (a, k) for a, k, _vf, _ron, ref in c.diodes if ref.endswith(".body")}
    assert body == {
        "Q1.body": ("S1", "D1N"),
        "Q2.body": ("D2N", "S2"),
        "Q3.body": ("S3", "D3N"),
    }


def test_a_reverse_biased_body_diode_changes_nothing() -> None:
    """A low-side N-FET off, blocking 12 V: the same answer with or without the body."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "+12V", "LOAD")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("GATE", "G"), "2": ("GND", "S"), "3": ("LOAD", "D")},
    )
    nl = netlist(toy)
    plain = build(nl).fix("GND", 0).fix("+12V", 12).fix("GATE", 0).solve()
    ext = build(nl, extended=True).fix("GND", 0).fix("+12V", 12).fix("GATE", 0).solve()
    assert ext["LOAD"] == pytest.approx(plain["LOAD"], rel=1e-12)
    assert ext.diode_current("Q1.body") == 0.0


def test_a_forward_biased_body_diode_conducts() -> None:
    """A P-FET off with its drain pulled above its source conducts through the body."""
    toy = Toy()
    toy.add(
        "Q1",
        "P",
        "Device:Q_PMOS_GSD",
        {"1": ("G", "G"), "2": ("OUT", "S"), "3": ("IN", "D")},
    )
    check_toy.resistor(toy, "R1", "1k", "OUT", "GND")
    sol = (
        build(netlist(toy), extended=True)
        .fix("GND", 0)
        .fix("IN", 5.0)
        .fix("G", 5.0)
        .solve()
    )
    assert sol.diode_current("Q1.body") > 0
    assert sol["OUT"] == pytest.approx(5.0 - 0.7, abs=0.05)


def test_extended_solves_a_board_of_new_symbols() -> None:
    """A Device:D from 12 V into a divider of R_Small: 11.3 V across 2k, 5.65 V."""
    toy = Toy()
    toy.add("D1", "D", "Device:D", {"1": ("K", "K"), "2": ("VIN", "A")})
    two_pad(toy, "R1", "Device:R_Small", "1k", "K", "MID")
    two_pad(toy, "R2", "Device:R_US", "1k", "MID", "GND")
    sol = build(netlist(toy), extended=True).fix("VIN", 12).fix("GND", 0).solve()
    assert sol["MID"] == pytest.approx(5.65, abs=0.02)


@pytest.mark.parametrize(
    "add",
    [
        lambda c: c.R("A", "B", 1000.0, "R1"),
        lambda c: c.D("A", "B", 0.7, ref="R1"),
        lambda c: c.fets.append(
            {"ref": "R1", "kind": "n", "g": "A", "d": "B", "s": "B"}
        ),
        lambda c: c.fets.extend(
            [{"ref": "Q1", "kind": "n", "g": "G", "d": "D", "s": "S"}]
        ),
        lambda c: c.D("S", "D", 0.7, ref="Q1.body"),
        lambda c: c.D("S", "D", 0.7, ref="Q1.b2"),
    ],
)
def test_extended_refuses_a_part_modelled_twice(add: object) -> None:
    """A hand model of a part ``build`` already holds raises, naming it."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "A", "B")
    toy.add(
        "Q1",
        "2N7002",
        "Transistor_FET:2N7002",
        {"1": ("G", "G"), "2": ("S", "S"), "3": ("D", "D")},
    )
    c = build(netlist(toy), extended=True)
    with pytest.raises(ValueError, match=r"(R1|Q1) is already modelled"):
        add(c)  # type: ignore[operator]


def test_extended_still_takes_new_parts_and_unnamed_elements() -> None:
    """A part ``build`` does not hold, a scenario's own name, an empty ref: fine."""
    toy = Toy()
    check_toy.resistor(toy, "R1", "1k", "A", "B")
    c = build(netlist(toy), extended=True)
    c.R("A", "GND", 100.0, "U1")
    c.R("A", "GND", 100.0, "load")
    c.R("A", "GND", 100.0, "")
    c.D("A", "GND", 0.7, ref="U2")
    c.fets.append({"ref": "U3", "kind": "n", "g": "A", "d": "B", "s": "GND"})
    with pytest.raises(ValueError, match="U1 is already modelled"):
        c.R("A", "GND", 100.0, "U1")


def test_an_extended_circuit_can_be_deep_copied_and_keeps_its_guard() -> None:
    """The fault-state check copies every circuit for its smaller-leak solve."""
    import copy

    from pcbkit.check import faults

    c = build(netlist(new_symbols()), extended=True)
    c.fix("G3", 5.0).fix("S3", 0.0)
    twin = copy.deepcopy(c)
    assert twin.fets == c.fets and twin.fets is not c.fets
    assert faults.low_leak_solution(c)["G3"] == 5.0
    with pytest.raises(ValueError, match="Q3 is already modelled"):
        twin.fets.append({"ref": "Q3", "kind": "n", "g": "G", "d": "D", "s": "S"})
    twin.D("X", "Y", 0.7, ref="NEW")
    assert "NEW" not in c._added


def test_extended_lists_the_parts_it_models_without_breakdown() -> None:
    """Zeners and TVSs, forward only or open: ``forward_only``; nothing by default."""
    nl = netlist(new_symbols())
    assert build(nl).forward_only == set()
    assert build(nl, extended=True).forward_only == {"D3", "D4", "D5"}


def test_extended_takes_one_breakdown_element_for_a_forward_only_part() -> None:
    """``D3.breakdown`` is a declared addition, not a second model; twice is refused."""
    c = build(netlist(new_symbols()), extended=True)
    c.D("K3", "A3", 5.1, ref="D3.breakdown")
    with pytest.raises(ValueError, match=r"D3\.breakdown is already modelled"):
        c.D("K3", "A3", 5.1, ref="D3.breakdown")
    with pytest.raises(ValueError, match="D1 is already modelled"):
        c.D("K1", "A1", 5.1, ref="D1.breakdown")


def library_diodes() -> Toy:
    """Return Diode and Power_Protection parts, two of them not two-pad A/K or A1/A2."""
    toy = Toy()
    toy.add("D11", "BZX84", "Diode:BZX84Cxx", {"1": ("K11", "K"), "2": ("A11", "A")})
    toy.add(
        "D12", "SMAJ", "Power_Protection:SMAJ", {"1": ("T1", "A1"), "2": ("T2", "A2")}
    )
    toy.add("D13", "X", "Diode:Unnamed", {"1": ("U1", "~"), "2": ("U2", "~")})
    toy.add(
        "D14",
        "BAT54C",
        "Diode:BAT54C",
        {"1": ("P1", "A"), "2": ("P2", "A"), "3": ("P3", "K")},
    )
    toy.add(
        "D15",
        "SMA3",
        "Diode:ThreePad",
        {"1": ("Q1", "A"), "2": ("Q2", "K"), "3": ("Q2", "K")},
    )
    return toy


def test_extended_models_two_pad_diode_and_protection_parts_as_kind_unknown() -> None:
    """A/K forward, A1/A2 open, both in ``forward_only``; unnamed and 3-pad left out.

    D15 is one diode on three pads (two cathode pads on one net), and it is still left
    out: only two-pad parts are modelled from these libraries.
    """
    nl = netlist(library_diodes())
    assert (build(nl).res, build(nl).diodes, build(nl).forward_only) == ([], [], set())
    c = build(nl, extended=True)
    assert [(a, k, ref) for a, k, _vf, _ron, ref in c.diodes] == [("A11", "K11", "D11")]
    assert [(a, b, ref) for a, b, _r, ref in c.res] == [("T1", "T2", "D12.open")]
    assert c.forward_only == {"D11", "D12"}
