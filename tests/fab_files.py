"""Helpers for tests that need a board's exported fab files, made up in a temp folder.

The job file has the shape KiCad 10 writes (``GeneralSpecs``, ``DesignRules``,
``MaterialStackup``), the outline Gerber and the Excellon files carry just the lines
pcbkit reads, and the BOM is written by the real ``write_csv``. Nothing needs KiCad.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pcbkit.fab import bom
from pcbkit.fab.bom import BomLine

STEM = "demo"
FAB_NAME = "My_Board_revA"  # what tests.board_files.TOML names


def bom_line(
    item: int,
    qty: int,
    refs: str,
    footprint: str = "R_0603_1608Metric",
    *,
    through_hole: bool = False,
    mpn: str = "PART",
) -> BomLine:
    """Return a BOM line for the comma-separated ``refs``."""
    return BomLine(
        item=item,
        qty=qty,
        refs=tuple(refs.split(",")),
        mfr="Acme",
        mpn=mpn,
        value="v",
        desc="d",
        footprint=footprint,
        through_hole=through_hole,
    )


def job_file(
    *,
    size: tuple[float, float] = (30.1, 20.1),
    layers: int = 2,
    thickness: float = 1.6,
    copper: Sequence[float] = (0.035, 0.035),
    finish: str | None = "HAL lead-free",
    rules: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a job file as KiCad writes it; None leaves ``finish`` or ``rules`` out."""
    general: dict[str, Any] = {
        "ProjectId": {"Name": STEM, "GUID": "00000000-0000-0000-0000-000000000000"},
        "Size": {"X": size[0], "Y": size[1]},
        "LayerNumber": layers,
        "BoardThickness": thickness,
    }
    if finish is not None:
        general["Finish"] = finish
    stackup: list[dict[str, Any]] = [{"Type": "Legend", "Name": "Top Silk Screen"}]
    names = ["F.Cu", "B.Cu"]
    for thick, name in zip(copper, names):
        stackup.append({"Type": "Copper", "Thickness": thick, "Name": name})
    job: dict[str, Any] = {
        "Header": {"GenerationSoftware": {"Vendor": "KiCad", "Version": "10.0.6"}},
        "GeneralSpecs": general,
        "FilesAttributes": [],
        "MaterialStackup": stackup,
    }
    if rules is None:
        rules = [
            {
                "Layers": "Outer",
                "PadToPad": 0.2,
                "PadToTrack": 0.2,
                "TrackToTrack": 0.2,
                "MinLineWidth": 0.2,
                "TrackToRegion": 0.25,
                "RegionToRegion": 0.25,
            }
        ]
    if rules:
        job["DesignRules"] = list(rules)
    return job


def write_gerber_zip(
    path: Path,
    *,
    job: dict[str, Any] | None = None,
    stroke: float | None = 0.1,
    plated: Sequence[float] = (0.3, 0.8),
    unplated: Sequence[float] = (3.2,),
) -> None:
    """Write a zip with a job file, an outline Gerber and two Excellon files.

    ``stroke`` is the outline line width (None leaves the outline file out); a hole list
    that is empty leaves its drill file out.
    """
    members: dict[str, str] = {
        f"{STEM}-job.gbrjob": json.dumps(job or job_file(), indent=2),
        f"{STEM}-F_Cu.gbr": "%FSLAX46Y46*%\n%ADD10C,0.250000*%\nM02*\n",
    }
    if stroke is not None:
        members[f"{STEM}-Edge_Cuts.gbr"] = (
            "%TF.FileFunction,Profile,NP*%\n%FSLAX46Y46*%\n%TA.AperFunction,Profile*%\n"
            f"%ADD10C,{stroke:f}*%\n%TD*%\nD10*\nX0Y0D02*\nM02*\n"
        )
    for name, sizes in (("PTH", plated), ("NPTH", unplated)):
        if sizes:
            tools = "".join(f"T{n}C{d:.3f}\n" for n, d in enumerate(sizes, start=1))
            members[f"{STEM}-{name}.drl"] = f"M48\nMETRIC\n{tools}%\nG90\nT1\nM30\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)


def write_fab_outputs(
    folder: Path,
    lines: Sequence[BomLine],
    fab_name: str = FAB_NAME,
    **gerber: Any,
) -> None:
    """Write the Gerber zip and the BOM CSV, named as an export names them."""
    folder.mkdir(parents=True, exist_ok=True)
    write_gerber_zip(folder / f"{fab_name}_gerbers.zip", **gerber)
    bom.write_csv(folder / f"{fab_name}_BOM.csv", lines)
