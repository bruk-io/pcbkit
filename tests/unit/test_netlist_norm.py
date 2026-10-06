"""Unit tests for tests/netlist_norm.py, the helper the netlist comparisons rely on."""

from __future__ import annotations

from pathlib import Path

from tests.netlist_norm import normalise

NETLIST = """\
(export (version "E")
  (design
    (source "/somewhere/x.kicad_sch")
    (date "2026-10-06T12:00:00")
    (tool "Eeschema 10.0.6"))
  (components
    (comp (ref "R1") (value "330") (footprint "Lib:Fp")
      (fields
        (field (name "Footprint") "Lib:Fp")
        (field (name "Datasheet") "")
        (field (name "MPN") "X-1"))
      (libsource (lib "Device") (part "R") (description "Resistor"))
      (sheetpath (names "/") (tstamps "/"))
      (tstamps "00000000-0000-0000-0000-000000000001"))
    (comp (ref "J1") (value "Hdr")
      (libsource (lib "Device") (part "C") (description "x"))))
  (nets
    (net (code "1") (name "/B") (class "Default")
      (node (ref "R1") (pin "2") (pinfunction "~") (pintype "passive"))
      (node (ref "J1") (pin "1") (pinfunction "Pin_1") (pintype "passive")))
    (net (code "2") (name "unconnected-(J1-Pin_2-Pad2)") (class "Default")
      (node (ref "J1") (pin "2")))))
"""


def test_components_and_nets_are_reduced_to_what_a_test_compares(
    tmp_path: Path,
) -> None:
    """Keep value, footprint, fields and sorted pins; drop the dates and UUIDs."""
    path = tmp_path / "x.net"
    path.write_text(NETLIST)
    assert normalise(path) == {
        "components": {
            "R1": {
                "value": "330",
                "footprint": "Lib:Fp",
                "fields": {"Footprint": "Lib:Fp", "Datasheet": "", "MPN": "X-1"},
            },
            "J1": {"value": "Hdr", "footprint": "", "fields": {}},
        },
        "nets": {
            "/B": ["J1.1", "R1.2"],
            "unconnected-(J1-Pin_2-Pad2)": ["J1.2"],
        },
    }
