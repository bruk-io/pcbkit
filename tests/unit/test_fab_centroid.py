"""Unit tests for pcbkit.fab.centroid: the pure parts, on kicad-cli's real CSV layout.

``body_centres`` and ``export_centroid`` need pcbnew and are tested against a real board
in tests/integration/test_fab_real_kicad.py.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pcbkit.fab import centroid

# What `kicad-cli pcb export pos --format csv` writes: quoted text, six decimals.
RAW = """\
Ref,Val,Package,PosX,PosY,Rot,Side
"C1","100n","C_0603_1608Metric",12.500000,5.250000,90.000000,top
"J1","Header","PinHeader_1x03_P2.54mm_Vertical",3.000000,7.000000,0.000000,top
"MH1","M3","MountingHole_3.2mm_M3",1.000000,1.000000,0.000000,top
"R1","10k","R_0603_1608Metric",8.000000,10.000000,180.000000,top
"""


def raw_rows(tmp_path: Path) -> list[dict[str, str]]:
    """Write RAW to a file and read it back the way export_centroid does."""
    path = tmp_path / "pos.csv"
    path.write_text(RAW, encoding="utf-8")
    return centroid.read_positions(path)


def test_the_position_file_is_read_by_column_name(tmp_path: Path) -> None:
    rows = raw_rows(tmp_path)
    assert [row["Ref"] for row in rows] == ["C1", "J1", "MH1", "R1"]
    assert rows[0]["PosX"] == "12.500000" and rows[0]["Rot"] == "90.000000"
    assert rows[1]["Package"] == "PinHeader_1x03_P2.54mm_Vertical"


def test_only_the_references_asked_for_are_kept_in_the_order_kicad_wrote_them(
    tmp_path: Path,
) -> None:
    rows = centroid.centroid_rows(raw_rows(tmp_path), {"R1", "C1", "J1"}, {})
    assert [row[0] for row in rows] == ["C1", "J1", "R1"]  # MH1 is not in the BOM


def test_a_row_is_reference_x_y_top_rotation_value_footprint(tmp_path: Path) -> None:
    [row] = centroid.centroid_rows(raw_rows(tmp_path), {"C1"}, {})
    assert row == [
        "C1",
        "12.500000",  # kicad-cli's own text, not reformatted
        "5.250000",
        "Top",
        "90.000000",
        "100n",
        "C_0603_1608Metric",
    ]
    assert list(centroid.HEADER) == [
        "Designator",
        "Mid X (mm)",
        "Mid Y (mm)",
        "Layer",
        "Rotation",
        "Value",
        "Footprint",
    ]


def test_a_body_centre_replaces_x_and_y_with_four_decimals(tmp_path: Path) -> None:
    body = {"R1": (8.123456, 9.5)}
    rows = centroid.centroid_rows(raw_rows(tmp_path), {"C1", "R1"}, body)
    by_ref = {row[0]: row for row in rows}
    assert by_ref["R1"][1:3] == ["8.1235", "9.5000"]
    assert by_ref["R1"][4:] == ["180.000000", "10k", "R_0603_1608Metric"]
    assert by_ref["C1"][1:3] == ["12.500000", "5.250000"]  # no body centre: untouched


def test_a_body_centre_for_a_part_that_is_not_listed_adds_no_row(
    tmp_path: Path,
) -> None:
    rows = centroid.centroid_rows(raw_rows(tmp_path), {"C1"}, {"R1": (1.0, 2.0)})
    assert [row[0] for row in rows] == ["C1"]


def test_a_part_missing_from_the_position_file_is_simply_absent(tmp_path: Path) -> None:
    rows = centroid.centroid_rows(raw_rows(tmp_path), {"C1", "Z9"}, {})
    assert [row[0] for row in rows] == ["C1"]


def test_the_file_has_crlf_endings_and_the_header_first(tmp_path: Path) -> None:
    rows = centroid.centroid_rows(raw_rows(tmp_path), {"R1", "C1"}, {})
    out = tmp_path / "centroid.csv"
    centroid.write_centroid(out, rows)
    raw = out.read_bytes()
    assert raw.startswith(b"Designator,Mid X (mm),Mid Y (mm),Layer,Rotation,Value,")
    assert raw.count(b"\r\n") == 3 and raw.count(b"\n") == 3
    with open(out, newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle))[2][0] == "R1"


def test_a_value_with_a_comma_is_quoted(tmp_path: Path) -> None:
    out = tmp_path / "centroid.csv"
    centroid.write_centroid(out, [["C9", "1", "2", "Top", "0", "1u, 25V", "C_0603"]])
    assert '"1u, 25V"' in out.read_text(encoding="utf-8")
