"""The files sent to the fab house must describe the board that passed the checks.

Switched on by ``outputs`` in ``[checks] groups``. The Gerbers, drill files, BOM and
centroid are parsed back and compared with the board and the schematic, so a stale or
mismatched export fails here. They are read from ``out/fab`` (or ``fab``), named after
``[board] fab_name``; the stackup is ``[stackup]`` in pcbkit.toml; the board's size is
``layout.W`` and ``layout.H`` from the project's ``layout.py``.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pcbkit.check import _pcb
from pcbkit.check.plugin import ProjectModule
from pcbkit.kicad.board import OX, OY
from pcbkit.kicad.sexp import find, findall, parse
from pcbkit.project import Project

mm = _pcb.to_mm

LAYERS = [
    "F_Cu",
    "B_Cu",
    "F_Mask",
    "B_Mask",
    "F_Silkscreen",
    "B_Silkscreen",
    "F_Paste",
    "B_Paste",
    "Edge_Cuts",
]


@pytest.fixture(scope="module")
def fab_dir(project: Project) -> Path:
    """Return out/fab, or fab/ where the export has been packaged."""
    for folder in (project.out_dir / "fab", project.fab_dir):
        if folder.is_dir():
            return folder
    pytest.fail(
        f"no fab outputs under {project.root}: run `pcbkit finalize`", pytrace=False
    )


@pytest.fixture(scope="module")
def gerbers(project: Project, fab_dir: Path) -> dict[str, str]:
    """Return the Gerber zip's files by name, as text."""
    z = zipfile.ZipFile(fab_dir / f"{project.config.board.fab_name}_gerbers.zip")
    return {n: z.read(n).decode() for n in z.namelist()}


@pytest.fixture(scope="module")
def bom(project: Project, fab_dir: Path) -> dict[str, dict[str, str]]:
    """Return the BOM's rows by reference designator."""
    with open(fab_dir / f"{project.config.board.fab_name}_BOM.csv") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        for d in r["Designator"].split(","):
            out[d] = r
    return out


@pytest.fixture(scope="module")
def centroid(project: Project, fab_dir: Path) -> dict[str, dict[str, str]]:
    """Return the centroid file's rows by reference designator."""
    with open(fab_dir / f"{project.config.board.fab_name}_centroid.csv") as f:
        return {r["Designator"]: r for r in csv.DictReader(f)}


@pytest.fixture(scope="module")
def sch_bom_flags(project: Project) -> dict[str, dict[str, Any]]:
    """Return, per schematic symbol, whether it is in the BOM, its value and its MPN."""
    schematic = project.kicad_dir / f"{project.config.board.stem}.kicad_sch"
    tree = parse(schematic.read_text(encoding="utf-8"))
    out = {}
    for sym in findall(tree, "symbol"):
        props = {str(p[1]): str(p[2]) for p in findall(sym, "property")}
        ref = props.get("Reference", "")
        in_bom = find(sym, "in_bom")
        dnp = find(sym, "dnp")
        out[ref] = {
            "in_bom": (in_bom is None or in_bom[1] == "yes")
            and not (dnp and dnp[1] == "yes"),
            "value": props.get("Value"),
            "mpn": props.get("MPN", ""),
        }
    return out


def test_gerber_set_complete(gerbers: dict[str, str]) -> None:
    """Have every layer's Gerber and both drill files in the zip."""
    names = set(gerbers)
    for layer in LAYERS:
        assert any(n.endswith(f"-{layer}.gbr") for n in names), f"missing {layer}"
    assert any(n.endswith("-PTH.drl") for n in names)
    assert any(n.endswith("-NPTH.drl") for n in names)


def test_job_file_stackup(
    gerbers: dict[str, str], record: Any, project: Project
) -> None:
    """Describe the stackup of pcbkit.toml in the Gerber job file."""
    stackup = project.config.stackup
    job = json.loads(next(v for k, v in gerbers.items() if k.endswith(".gbrjob")))
    specs = job["GeneralSpecs"]
    copper = [
        lay["Thickness"] for lay in job["MaterialStackup"] if lay["Type"] == "Copper"
    ]
    record(
        "job",
        {
            "size": specs["Size"],
            "layers": specs["LayerNumber"],
            "copper_mm": copper,
            "thickness": specs["BoardThickness"],
        },
    )
    assert specs["LayerNumber"] == stackup.layers
    assert len(copper) == stackup.layers
    assert all(abs(t - stackup.copper_mm) < 1e-9 for t in copper), (
        f"job file copper {copper}, pcbkit.toml says {stackup.copper_mm}"
    )
    assert abs(specs["BoardThickness"] - stackup.thickness_mm) < 1e-9


def _gerber_coords(text: str) -> list[tuple[float, float]]:
    """Return every point a Gerber file draws or moves to, in millimetres."""
    fs = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", text)
    dec = int(fs.group(2))
    pts = []
    x = y = 0.0
    for m in re.finditer(r"(?:X(-?\d+))?(?:Y(-?\d+))?(?:I-?\d+J-?\d+)?D0[123]\*", text):
        if m.group(1):
            x = int(m.group(1)) / 10**dec
        if m.group(2):
            y = int(m.group(2)) / 10**dec
        pts.append((x, y))
    return pts


def test_outline_size(gerbers: dict[str, str], layout: ProjectModule) -> None:
    """Cut the board to the size layout.py says."""
    edge = next(v for k, v in gerbers.items() if k.endswith("Edge_Cuts.gbr"))
    pts = _gerber_coords(edge)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert abs((max(xs) - min(xs)) - layout.W) < 0.05
    assert abs((max(ys) - min(ys)) - layout.H) < 0.05


def _excellon_hits(text: str) -> dict[float, int]:
    """Return how many holes of each diameter (mm) an Excellon drill file has."""
    tools = {t: float(d) for t, d in re.findall(r"^(T\d+)C([\d.]+)", text, re.M)}
    hits: dict[float, int] = {}
    cur = None
    for line in text.splitlines():
        if re.fullmatch(r"T\d+", line.strip()):
            cur = line.strip()
        elif line.startswith("X") and cur:
            d = round(tools[cur], 3)
            hits[d] = hits.get(d, 0) + 1
    return hits


def test_drill_files_match_board(
    gerbers: dict[str, str], board: Any, record: Any
) -> None:
    """Drill exactly the holes the board has, by diameter, plated and not."""
    pcbnew = _pcb.pcbnew()
    pth = _excellon_hits(next(v for k, v in gerbers.items() if k.endswith("-PTH.drl")))
    npth = _excellon_hits(
        next(v for k, v in gerbers.items() if k.endswith("-NPTH.drl"))
    )
    want_p: dict[float, int] = {}
    want_n: dict[float, int] = {}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            d = round(mm(t.GetDrillValue()), 3)
            want_p[d] = want_p.get(d, 0) + 1
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.HasHole():
                d = round(min(mm(p.GetDrillSizeX()), mm(p.GetDrillSizeY())), 3)
                tgt = want_p if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH else want_n
                tgt[d] = tgt.get(d, 0) + 1
    record("drills", {"pth": pth, "npth": npth})
    assert pth == want_p, f"PTH drill file {pth} vs board {want_p}"
    assert npth == want_n


def test_bom_matches_schematic(
    bom: dict[str, dict[str, str]], sch_bom_flags: dict[str, dict[str, Any]]
) -> None:
    """List in the BOM the parts the schematic puts in it, with the schematic's MPNs."""
    want = {
        r for r, f in sch_bom_flags.items() if f["in_bom"] and not r.startswith("#")
    }
    got = set(bom)
    assert got == want, f"BOM extra {sorted(got - want)} missing {sorted(want - got)}"
    stale = []
    for ref in want:
        mpn = sch_bom_flags[ref]["mpn"]
        # an MPN with a space is free text (a description), not a part number to compare
        if mpn and " " not in mpn and mpn != bom[ref]["Manufacturer Part Number"]:
            stale.append((ref, mpn, bom[ref]["Manufacturer Part Number"]))
    assert not stale, f"BOM part numbers differ from the schematic: {stale}"


def test_centroid_matches_board(
    board: Any,
    centroid: dict[str, dict[str, str]],
    bom: dict[str, dict[str, str]],
    record: Any,
    layout: ProjectModule,
) -> None:
    """Place every SMD part in the centroid file where the board has it."""
    height = layout.H
    parts = {f.GetReference(): f for f in board.GetFootprints()}
    smd = {
        r
        for r, f in parts.items()
        if r in bom and all(not p.HasHole() for p in f.Pads())
    }
    assert smd <= set(centroid), (
        f"SMD parts missing from centroid: {sorted(smd - set(centroid))}"
    )
    off = []
    for ref, row in centroid.items():
        f = parts[ref]
        ax, ay = mm(f.GetX()) - OX, (OY + height) - mm(f.GetY())
        if ref in smd:
            boxes = [p.GetBoundingBox() for p in f.Pads()]
            cx = (
                min(mm(b.GetLeft()) for b in boxes)
                + max(mm(b.GetRight()) for b in boxes)
            ) / 2 - OX
            cy = (OY + height) - (
                min(mm(b.GetTop()) for b in boxes)
                + max(mm(b.GetBottom()) for b in boxes)
            ) / 2
            gx, gy = float(row["Mid X (mm)"]), float(row["Mid Y (mm)"])
            # placement point must sit on the package, near the pad-pattern centre
            if ((gx - cx) ** 2 + (gy - cy) ** 2) ** 0.5 > 0.5:
                off.append((ref, round(((ax - cx) ** 2 + (ay - cy) ** 2) ** 0.5, 2)))
            continue
        assert (
            abs(float(row["Mid X (mm)"]) - ax) < 0.02
            and abs(float(row["Mid Y (mm)"]) - ay) < 0.02
        ), f"{ref} centroid is stale"
    for ref, row in centroid.items():
        got = float(row["Rotation"]) % 360
        assert abs(got - parts[ref].GetOrientationDegrees() % 360) < 0.01
    record("SMD placement point >0.5 mm from pad centre", off)
    assert not off, f"the fab house expects part centres; these are off-centre: {off}"
