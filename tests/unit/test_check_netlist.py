"""Unit tests for pcbkit.check.netlist, on netlists written out in the tests.

The texts are synthetic (generic parts such as R1, C1 and U1 on nets such as VIN and
GND) but they have the layout kicad-cli writes, which was copied from a real KiCad
10.0.6 export; one test reads an excerpt of such an export as it was written. The two
KiCad generations differ in the one way the reader has to handle: KiCad 10 writes a
pin's function as ``<name>_<pin number>`` and KiCad 9 writes ``<name>``.

kicad-cli is faked for ``export_netlist`` and ``fresh_netlist``, as in
test_kicad_cli.py: the fake writes the file the real tool would write, and everything
the module does with that file is the real code.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pcbkit.check import netlist
from pcbkit.check.netlist import Netlist, natkey, parse_value
from pcbkit.kicad import cli, env
from pcbkit.kicad.env import Run
from tests.fake_machine import FakeMachine

KICAD_9 = "Eeschema 9.0.3"
KICAD_10 = "Eeschema 10.0.6"


# --- sample netlists -----------------------------------------------------------------

# An excerpt of what kicad-cli 10.0.6 wrote for tests/fixtures/tiny_board, layout and
# all (one field is only a name; passive pins have no pinfunction; the pin functions
# carry the KiCad 10 suffix). Only the title block and the library details are cut down.
REAL_KICAD_10 = """\
(export
  (version "E")
  (design
    (source "/work/tiny_board/kicad/tiny_board.kicad_sch")
    (date "2026-10-06T21:50:51")
    (tool "Eeschema 10.0.6")
    (sheet
      (number "1")
      (name "/")
      (tstamps "/")
      (title_block
        (title "Tiny Board")
        (company)
        (rev "A")
        (comment
          (number "1")
          (value "")
        )
      )
    )
  )
  (components
    (comp
      (ref "D1")
      (value "Green")
      (footprint "LED_SMD:LED_0603_1608Metric")
      (description "LED Green 0603")
      (fields
        (field
          (name "MPN") "GRN-0603")
        (field
          (name "Manufacturer") "Acme")
        (field
          (name "Footprint") "LED_SMD:LED_0603_1608Metric")
        (field
          (name "Datasheet")
        )
        (field
          (name "Description") "LED Green 0603")
      )
      (libsource
        (lib "Device")
        (part "LED")
        (description "Light emitting diode")
      )
      (property
        (name "ki_keywords")
        (value "LED diode")
      )
      (sheetpath
        (names "/")
        (tstamps "/")
      )
      (tstamps "3331d2e8-1b00-5465-ac0a-92a9c3e6bc0b")
      (units
        (unit
          (name "A")
          (pins
            (pin
              (num "1")
            )
            (pin
              (num "2")
            )
          )
        )
      )
    )
    (comp
      (ref "J1")
      (value "Supply")
      (footprint "tiny:Header_1x02_P2.54mm")
      (description "Supply header: 3V3 / GND")
      (fields
        (field
          (name "MPN") "HDR-1X02")
        (field
          (name "Datasheet")
        )
      )
      (libsource
        (lib "tiny")
        (part "Header_1x02")
        (description "Two-pin header")
      )
      (sheetpath
        (names "/")
        (tstamps "/")
      )
      (tstamps "a4198aaf-dc9f-5d18-a05f-5a2a464f53c9")
    )
    (comp
      (ref "R1")
      (value "330")
      (footprint "tiny:R_Vendored")
      (description "Resistor 330 0603 1%")
      (libsource
        (lib "Device")
        (part "R")
        (description "Resistor")
      )
      (sheetpath
        (names "/")
        (tstamps "/")
      )
      (tstamps "00000000-0000-0000-0000-000000000001")
    )
  )
  (nets
    (net
      (code "1")
      (name "/+3V3")
      (class "Default")
      (node
        (ref "J1")
        (pin "1")
        (pinfunction "Pin_1_1")
        (pintype "passive")
      )
      (node
        (ref "R1")
        (pin "1")
        (pintype "passive")
      )
    )
    (net
      (code "2")
      (name "/GND")
      (class "Default")
      (node
        (ref "D1")
        (pin "1")
        (pinfunction "K_1")
        (pintype "passive")
      )
      (node
        (ref "J1")
        (pin "2")
        (pinfunction "Pin_2_2")
        (pintype "passive")
      )
    )
    (net
      (code "3")
      (name "/LED_A")
      (class "Default")
      (node
        (ref "D1")
        (pin "2")
        (pinfunction "A_2")
        (pintype "passive")
      )
      (node
        (ref "R1")
        (pin "2")
        (pintype "passive")
      )
    )
  )
)
"""


def pin_node(ref: str, pin: str, name: str | None, suffixed: bool) -> str:
    """Return a ``(node ...)`` as the given KiCad generation writes it.

    ``name`` is the pin's own name, or None for an unnamed ("~") pin. KiCad 10 writes
    ``<name>_<pin>`` and leaves out the function of an unnamed pin; KiCad 9 writes the
    name, and ``~`` for an unnamed pin.
    """
    if name is None:
        function = "" if suffixed else '(pinfunction "~") '
    else:
        written = f"{name}_{pin}" if suffixed else name
        function = f'(pinfunction "{written}") '
    return f'(node (ref "{ref}") (pin "{pin}") {function}(pintype "passive"))'


def netlist_text(tool: str, suffixed: bool) -> str:
    """Return a small netlist as ``tool`` writes it.

    Three resistors (listed R1, R10, R2, so the netlist's order is not the natural
    one), two capacitors and a three-pin IC whose pin 1 is called ``IO_1``: a name
    that ends in its own pin number, which a careless reader would cut short.
    """

    def pin(ref: str, number: str, name: str | None = None) -> str:
        return pin_node(ref, number, name, suffixed)

    return f"""\
(export (version "E")
  (design
    (source "/work/demo.kicad_sch")
    (date "2026-10-06T12:00:00")
    (tool "{tool}"))
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric")
      (fields
        (field (name "MPN") "PART-R10K")
        (field (name "Datasheet")))
      (libsource (lib "Device") (part "R") (description "Resistor")))
    (comp (ref "R10") (value "100")
      (libsource (lib "Device") (part "R") (description "Resistor")))
    (comp (ref "R2") (value "4k7")
      (libsource (lib "Device") (part "R") (description "Resistor")))
    (comp (ref "C1") (value "100n") (footprint "Capacitor_SMD:C_0603_1608Metric")
      (libsource (lib "Device") (part "C") (description "Capacitor")))
    (comp (ref "C2") (value "10u 25V")
      (libsource (lib "Device") (part "C_Polarized") (description "Capacitor")))
    (comp (ref "U1") (value "Reg3") (footprint "Package_TO_SOT_SMD:SOT-23")
      (libsource (lib "Test") (part "IC3") (description "Three pin IC"))))
  (nets
    (net (code "1") (name "/VIN") (class "Default")
      {pin("R1", "1")}
      {pin("R10", "1")}
      {pin("C1", "1")}
      {pin("U1", "3", "VIN")})
    (net (code "2") (name "/NET_A") (class "Default")
      {pin("R1", "2")}
      {pin("R2", "1")}
      {pin("U1", "1", "IO_1")})
    (net (code "3") (name "/GND") (class "Default")
      {pin("R2", "2")}
      {pin("C1", "2")}
      {pin("C2", "2")}
      {pin("U1", "2", "GND")})
    (net (code "4") (name "/SUB/NET_B") (class "Default")
      {pin("R10", "2")})
    (net (code "5") (name "Net-(C2-Pad1)") (class "Default")
      {pin("C2", "1")})))
"""


def one_pin_netlist(design: str, pin: str, function: str) -> str:
    """Return a netlist of one IC with one node, under the given ``design`` section."""
    return (
        f'(export (version "E") {design} '
        '(components (comp (ref "U1") (value "X") '
        '(libsource (lib "Test") (part "IC")))) '
        '(nets (net (code "1") (name "/N") '
        f'(node (ref "U1") (pin "{pin}") (pinfunction "{function}")))))'
    )


@pytest.fixture
def kicad9() -> Netlist:
    """Return the sample netlist as KiCad 9.0.3 writes it."""
    return Netlist.from_text(netlist_text(KICAD_9, suffixed=False))


@pytest.fixture
def kicad10() -> Netlist:
    """Return the sample netlist as KiCad 10.0.6 writes it."""
    return Netlist.from_text(netlist_text(KICAD_10, suffixed=True))


# --- pin functions: the KiCad 10 suffix ----------------------------------------------


def test_kicad_10_pin_functions_lose_the_number_kicad_added(kicad10: Netlist) -> None:
    """Read ``VIN_3`` on pin 3 as ``VIN``, and ``IO_1_1`` on pin 1 as ``IO_1``."""
    assert kicad10.nets["VIN"] == [
        ("R1", "1", ""),
        ("R10", "1", ""),
        ("C1", "1", ""),
        ("U1", "3", "VIN"),
    ]
    assert ("U1", "1", "IO_1") in kicad10.nets["NET_A"]
    assert ("U1", "2", "GND") in kicad10.nets["GND"]


def test_kicad_9_pin_functions_are_kept_as_written(kicad9: Netlist) -> None:
    """Keep ``IO_1`` on pin 1 as it is: KiCad 9 added no suffix, so none is removed."""
    assert kicad9.nets["VIN"] == [
        ("R1", "1", "~"),
        ("R10", "1", "~"),
        ("C1", "1", "~"),
        ("U1", "3", "VIN"),
    ]
    assert ("U1", "1", "IO_1") in kicad9.nets["NET_A"]
    assert ("U1", "2", "GND") in kicad9.nets["GND"]


def test_the_two_generations_give_the_same_connectivity_and_parts(
    kicad9: Netlist, kicad10: Netlist
) -> None:
    """Differ only in the text of the pin functions, not in what is connected."""
    assert kicad9.pin_net == kicad10.pin_net
    assert kicad9.parts == kicad10.parts
    for name, nodes in kicad9.nets.items():
        assert [(r, p) for r, p, _ in nodes] == [
            (r, p) for r, p, _ in kicad10.nets[name]
        ]


@pytest.mark.parametrize(
    ("tool", "pin", "written", "expected"),
    [
        # KiCad 9 and older: the name as written, even when it ends in "_<pin>"
        ("Eeschema 9.0.3", "3", "VIN_3", "VIN_3"),
        ("Eeschema 9.0.3", "1", "IO_1", "IO_1"),
        ("Eeschema 9.99.0", "2", "GND_2", "GND_2"),
        ("Eeschema (7.0.2)", "2", "GND_2", "GND_2"),
        # KiCad 10 and later: the one "_<pin>" KiCad added comes off the end
        ("Eeschema 10.0.6", "3", "VIN_3", "VIN"),
        ("Eeschema 10.0.0-rc1", "3", "VIN_3", "VIN"),
        ("Eeschema 11.2.0", "3", "VIN_3", "VIN"),
        ("Eeschema 10.0.6", "1", "IO_1_1", "IO_1"),
        ("Eeschema 10.0.6", "12", "A_12", "A"),
        ("Eeschema 10.0.6", "A1", "BALL_A1", "BALL"),
        # ... but only this pin's own number, with its underscore
        ("Eeschema 10.0.6", "3", "VIN", "VIN"),
        ("Eeschema 10.0.6", "3", "VIN_2", "VIN_2"),
        ("Eeschema 10.0.6", "3", "VIN_13", "VIN_13"),
    ],
)
def test_the_suffix_is_removed_only_when_the_header_says_kicad_10_or_later(
    tool: str, pin: str, written: str, expected: str
) -> None:
    """Gate the removal on the major version in the netlist's own ``tool`` line."""
    text = one_pin_netlist(f'(design (source "x") (tool "{tool}"))', pin, written)
    assert Netlist.from_text(text).nets["N"] == [("U1", pin, expected)]


@pytest.mark.parametrize("design", ['(design (source "x"))', ""])
def test_a_netlist_that_does_not_say_who_wrote_it_keeps_its_pin_functions(
    design: str,
) -> None:
    """Leave the names alone when there is no ``tool`` line, or no header at all."""
    nl = Netlist.from_text(one_pin_netlist(design, "3", "VIN_3"))
    assert nl.nets["N"] == [("U1", "3", "VIN_3")]


def test_a_real_kicad_10_export_is_read_as_the_checks_need_it() -> None:
    """Read the genuine layout: nodes over many lines, a field with no text."""
    nl = Netlist.from_text(REAL_KICAD_10)
    assert list(nl.parts) == ["D1", "J1", "R1"]
    assert nl.kind("D1") == "Device:LED"
    assert nl.parts["D1"]["fields"] == {
        "MPN": "GRN-0603",
        "Manufacturer": "Acme",
        "Footprint": "LED_SMD:LED_0603_1608Metric",
        "Datasheet": "",
        "Description": "LED Green 0603",
    }
    assert nl.parts["R1"]["fields"] == {}
    assert nl.nets["+3V3"] == [("J1", "1", "Pin_1"), ("R1", "1", "")]
    assert nl.nets["GND"] == [("D1", "1", "K"), ("J1", "2", "Pin_2")]
    assert nl.nets["LED_A"] == [("D1", "2", "A"), ("R1", "2", "")]
    assert nl.net("D1", 2) == "LED_A"
    assert nl.refs_on("GND") == {"D1", "J1"}


# --- nets, parts and lookups ---------------------------------------------------------


def test_the_leading_slash_of_a_net_name_is_removed_and_no_other(
    kicad10: Netlist,
) -> None:
    """Strip "/" from "/VIN" but keep the one inside "/SUB/NET_B" and plain names."""
    assert sorted(kicad10.nets) == [
        "GND",
        "NET_A",
        "Net-(C2-Pad1)",
        "SUB/NET_B",
        "VIN",
    ]
    assert kicad10.pin_net[("R10", "2")] == "SUB/NET_B"
    assert kicad10.pin_net[("C2", "1")] == "Net-(C2-Pad1)"
    assert kicad10.pin_net[("R1", "1")] == "VIN"


def test_nets_keep_the_order_of_the_netlist(kicad10: Netlist) -> None:
    """List nodes as the netlist does, because the DC solver depends on it."""
    assert [r for r, _, _ in kicad10.nets["GND"]] == ["R2", "C1", "C2", "U1"]
    assert list(kicad10.nets) == [
        "VIN",
        "NET_A",
        "GND",
        "SUB/NET_B",
        "Net-(C2-Pad1)",
    ]


def test_parts_hold_value_footprint_library_symbol_and_fields(
    kicad10: Netlist,
) -> None:
    """Record each component's attributes, with "" for a footprint or text it lacks."""
    assert kicad10.parts["R1"] == {
        "ref": "R1",
        "value": "10k",
        "footprint": "Resistor_SMD:R_0603_1608Metric",
        "lib": "Device",
        "part": "R",
        "fields": {"MPN": "PART-R10K", "Datasheet": ""},
    }
    assert kicad10.parts["R10"]["footprint"] == ""
    assert kicad10.parts["R10"]["fields"] == {}
    assert kicad10.parts["C2"]["value"] == "10u 25V"
    assert (kicad10.parts["U1"]["lib"], kicad10.parts["U1"]["part"]) == ("Test", "IC3")
    assert list(kicad10.parts) == ["R1", "R10", "R2", "C1", "C2", "U1"]


def test_net_finds_the_net_of_a_pin_given_as_a_number_or_as_text(
    kicad10: Netlist,
) -> None:
    """Accept pin 1 as ``1`` or ``"1"``, and give None for a pin that is not listed."""
    assert kicad10.net("R1", 1) == "VIN"
    assert kicad10.net("R1", "2") == "NET_A"
    assert kicad10.net("U1", 3) == "VIN"
    assert kicad10.net("R1", 9) is None
    assert kicad10.net("X9", 1) is None


def test_refs_on_returns_the_references_with_a_pin_on_the_net(
    kicad10: Netlist,
) -> None:
    """Return a set of references, empty for a net that does not exist."""
    assert kicad10.refs_on("VIN") == {"R1", "R10", "C1", "U1"}
    assert kicad10.refs_on("SUB/NET_B") == {"R10"}
    assert kicad10.refs_on("/VIN") == set()  # the slash is not part of the name
    assert kicad10.refs_on("NOPE") == set()


def test_kind_is_the_library_a_colon_and_the_symbol(kicad10: Netlist) -> None:
    """Join library and symbol, and raise KeyError for a reference that is not there."""
    assert kicad10.kind("R1") == "Device:R"
    assert kicad10.kind("C2") == "Device:C_Polarized"
    assert kicad10.kind("U1") == "Test:IC3"
    with pytest.raises(KeyError):
        kicad10.kind("X9")


def test_by_kind_returns_references_in_natural_order(kicad10: Netlist) -> None:
    """Put R2 before R10 although the netlist lists R10 first."""
    assert list(kicad10.parts)[:3] == ["R1", "R10", "R2"]
    assert kicad10.by_kind("Device:R") == ["R1", "R2", "R10"]
    assert kicad10.by_kind("Device:C", "Device:C_Polarized") == ["C1", "C2"]
    assert kicad10.by_kind("Device:R", "Device:C") == ["C1", "R1", "R2", "R10"]
    assert kicad10.by_kind("Device:L") == []


def test_natkey_orders_by_the_number_inside_a_reference() -> None:
    """Compare ``R10`` as the text R and the number 10, not as characters."""
    assert natkey("R10") == ["R", 10, ""]
    refs = ["R10", "R2", "R1", "C3", "RN1", "R1A", "R100"]
    assert sorted(refs, key=natkey) == ["C3", "R1", "R1A", "R2", "R10", "R100", "RN1"]
    assert sorted(refs) != sorted(refs, key=natkey)  # plain sorting gets it wrong


def test_from_file_and_from_text_read_the_same_netlist(tmp_path: Path) -> None:
    """Read a netlist file given as a Path or as a string."""
    text = netlist_text(KICAD_10, suffixed=True)
    path = tmp_path / "demo.net"
    path.write_text(text, encoding="utf-8")
    expected = Netlist.from_text(text)
    for given in (path, str(path)):
        read = Netlist.from_file(given)
        assert read.parts == expected.parts
        assert read.nets == expected.nets
        assert read.pin_net == expected.pin_net


# --- values --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2.2k", 2200.0),
        ("4k7", 4700.0),
        ("1k5", 1500.0),
        ("10u 25V", 10e-6),
        ("100n", 100e-9),
        ("4x220", 220.0),
        ("1m", 1e-3),
        ("330", 330.0),
        ("0.1", 0.1),
        ("1M", 1e6),
        ("1M5", 1.5e6),
        ("4.7u", 4.7e-6),
        ("100nF", 100e-9),
        ("10pF", 10e-12),
        ("4µ7", 4.7e-6),  # the micro sign, as in 4u7
        ("47µF", 47e-6),
        ("10k 1%", 10e3),
        ("  470  ", 470.0),
    ],
)
def test_parse_value_reads_the_values_a_schematic_holds(
    text: str, value: float
) -> None:
    """Read an engineering-notation value, using only its first word."""
    assert parse_value(text) == pytest.approx(value, rel=1e-12)


def test_parse_value_tells_milli_from_mega_by_case() -> None:
    """Read ``m`` as 10^-3 and ``M`` as 10^6."""
    assert parse_value("1m") == pytest.approx(1e-3, rel=1e-12)
    assert parse_value("1M") == pytest.approx(1e6, rel=1e-12)
    assert parse_value("1M") / parse_value("1m") == pytest.approx(1e9, rel=1e-12)


def test_in_a_resistor_pack_value_the_part_after_the_x_is_the_value() -> None:
    """Take the 220 of ``4x220``: one element's value, not the count."""
    assert parse_value("4x220") == 220.0
    assert parse_value("4x4k7") == pytest.approx(4700.0, rel=1e-12)


@pytest.mark.parametrize("text", ["abc", "k", "N/A", "DNP", "~", "4x", "1.2.3"])
def test_parse_value_raises_value_error_for_text_that_is_not_a_value(
    text: str,
) -> None:
    """Fail loudly rather than return a number for a value it cannot read."""
    with pytest.raises(ValueError):
        parse_value(text)


# --- export_netlist and fresh_netlist, with a fake kicad-cli --------------------------


@dataclass
class FakeKicadCli:
    """A kicad-cli that records its command lines and writes ``text`` where told.

    ``text`` None makes it quiet: it exits 0 and writes nothing.
    """

    path: str
    text: str | None
    calls: list[list[str]] = field(default_factory=list)


@pytest.fixture
def kicad(machine: FakeMachine, monkeypatch: pytest.MonkeyPatch) -> FakeKicadCli:
    """Put a fake kicad-cli on the fake PATH that writes the KiCad 10 sample netlist."""
    path = str(
        machine.exe(machine.usr_bin / "kicad-cli", "10.0.6", on_path="kicad-cli")
    )
    fake = FakeKicadCli(path, netlist_text(KICAD_10, suffixed=True))

    def run(args: list[str], timeout: float = 20.0) -> Run:
        if args[1:] == ["--version"]:  # the probe in env.find_kicad_cli
            return machine.run(args, timeout)
        fake.calls.append(list(args))
        if fake.text is not None:
            Path(args[args.index("-o") + 1]).write_text(fake.text, encoding="utf-8")
        return Run(0)

    monkeypatch.setattr(env, "_run", run)
    return fake


def test_export_netlist_runs_kicad_cli_and_returns_the_file(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Ask for the s-expression netlist of the schematic, in that order of arguments."""
    sch, out = tmp_path / "demo.kicad_sch", tmp_path / "demo.net"
    result = netlist.export_netlist(sch, out)
    assert result == out
    assert isinstance(result, Path)
    assert out.read_text(encoding="utf-8") == kicad.text
    assert kicad.calls == [
        [
            kicad.path,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "-o",
            str(out),
            str(sch),
        ]
    ]


def test_export_netlist_accepts_strings_and_still_returns_a_path(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Turn a string output path into a Path."""
    out = tmp_path / "demo.net"
    result = netlist.export_netlist(str(tmp_path / "demo.kicad_sch"), str(out))
    assert result == out
    assert isinstance(result, Path)
    assert out.is_file()


def test_fresh_netlist_exports_into_the_folder_and_reads_the_result(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Write ``fresh.net`` in the given folder, and nothing next to the schematic."""
    proj, work = tmp_path / "proj", tmp_path / "work"
    proj.mkdir()
    work.mkdir()
    sch = proj / "demo.kicad_sch"
    nl = netlist.fresh_netlist(sch, work)
    assert isinstance(nl, Netlist)
    assert nl.parts["R1"]["value"] == "10k"
    assert nl.net("U1", 3) == "VIN"
    assert (work / "fresh.net").read_text(encoding="utf-8") == kicad.text
    assert kicad.calls[0][kicad.calls[0].index("-o") + 1] == str(work / "fresh.net")
    assert kicad.calls[0][-1] == str(sch)
    assert list(proj.iterdir()) == []  # nothing was written next to the schematic


def test_fresh_netlist_does_not_read_an_older_file(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Read what kicad-cli wrote now, not the stale ``.net`` files lying about."""
    stale = netlist_text(KICAD_10, suffixed=True).replace('"10k"', '"999k"')
    work = tmp_path / "work"
    work.mkdir()
    (work / "fresh.net").write_text(stale, encoding="utf-8")
    (tmp_path / "demo.net").write_text(stale, encoding="utf-8")
    nl = netlist.fresh_netlist(tmp_path / "demo.kicad_sch", work)
    assert nl.parts["R1"]["value"] == "10k"


def test_fresh_netlist_fails_when_kicad_cli_writes_nothing_even_if_a_stale_file_exists(
    kicad: FakeKicadCli, tmp_path: Path
) -> None:
    """Raise rather than fall back to the old ``fresh.net`` a quiet kicad-cli left."""
    kicad.text = None
    work = tmp_path / "work"
    work.mkdir()
    (work / "fresh.net").write_text(
        netlist_text(KICAD_10, suffixed=True), encoding="utf-8"
    )
    with pytest.raises(cli.KicadCliError, match="wrote nothing"):
        netlist.fresh_netlist(tmp_path / "demo.kicad_sch", work)
    assert not (work / "fresh.net").exists()


def test_fresh_netlist_without_a_folder_uses_a_new_temporary_one_each_time(
    kicad: FakeKicadCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give every call its own folder under the temporary directory."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    sch = tmp_path / "demo.kicad_sch"
    first = netlist.fresh_netlist(sch)
    second = netlist.fresh_netlist(sch)
    targets = [Path(call[call.index("-o") + 1]) for call in kicad.calls]
    assert [t.name for t in targets] == ["fresh.net", "fresh.net"]
    assert all(t.parent.parent == scratch for t in targets)
    assert targets[0].parent != targets[1].parent
    assert all(t.is_file() for t in targets)
    assert first.parts == second.parts
