"""Fab-house limits and board hygiene that KiCad's own DRC does not cover.

Switched on by ``fab`` in ``[checks] groups``. Everything below is read from the
project's ``specs.py``; a name that is missing fails the check that needs it, naming it.

* ``PCBWAY``: the fab house's limits (mm): ``min_annular``, ``min_drill``,
  ``min_plated_slot``, ``silk_min_height``, ``silk_min_stroke``, ``min_track_2oz`` and
  ``min_space_2oz`` (the stricter 2 oz track and space, so either copper weight works).
* ``WIRE_PADS``: ``{ref: (positive pad, negative pad)}`` for bare pads that take a wire;
  each needs a "+" and a "-" on the silkscreen, nearer its own pad than the other.
* ``DECOUPLING``: rows ``(ic, pin, (capacitor refs), limit mm)``: the nearest listed
  capacitor must have a pad that close to the pin.
* ``POWER_FOOTPRINTS``: one dict per part whose pads must match its datasheet, with a
  ``ref`` and any of ``tab_pad`` (the largest pad is this one) with ``tab_net`` (and on
  this net), ``smaller`` (pairs ``(small pad, large pad)``) and ``drill_mm``
  (``{pad: drill}``).
* ``I2C_BUSES``: ``{bus: (sda net, scl net, speed Hz, off-board devices)}``, with
  ``I2C_TR_MAX`` (the rise time limit per speed) and the capacitance model
  ``I2C_PIN_C``, ``TRACE_C_PER_MM``, ``I2C_CABLE_LEN`` and ``I2C_CABLE_C_PER_M``.
  Optional: ``I2C_PULLUP_NET`` ("+3V3") and ``I2C_DEVICE_PREFIXES`` ("UA": the
  references that count as devices on a bus).
* Optional: ``ASSEMBLY_TEXT_EXEMPT``: references whose pin labels may sit over their
  own through-hole pins on the assembly drawing (a socket's, say).

Stackup copper comes from ``[stackup] copper_mm`` in pcbkit.toml.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest

from pcbkit.check import _pcb
from pcbkit.check.netlist import Netlist, parse_value
from pcbkit.check.plugin import ProjectModule, spec_params
from pcbkit.kicad.board import OX, OY
from pcbkit.project import Project

mm = _pcb.to_mm


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the decoupling and I2C checks from the project's tables."""
    spec_params(metafunc, "ic,pin,caps,limit", lambda specs: specs.DECOUPLING)
    spec_params(metafunc, "bus", lambda specs: sorted(specs.I2C_BUSES))


def fps(board: Any) -> dict[str, Any]:
    """Return the board's footprints by reference."""
    return {f.GetReference(): f for f in board.GetFootprints()}


def pad(board: Any, ref: str, num: Any) -> Any:
    """Return pad ``num`` of footprint ``ref``."""
    return next(p for p in fps(board)[ref].Pads() if p.GetNumber() == str(num))


def xy(item: Any) -> tuple[float, float]:
    """Return an item's position in layout millimetres."""
    return mm(item.GetX()) - OX, mm(item.GetY()) - OY


# ------------------------------------------------------------------ fab limits
def test_annular_rings_and_drills_meet_pcbway(
    board: Any, record: Any, specs: ProjectModule
) -> None:
    """Keep annular rings, drills and plated slots inside the fab house's limits."""
    pcbnew = _pcb.pcbnew()
    limits = specs.PCBWAY
    rings, drills, slots = [], [], []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            d = mm(t.GetDrillValue())
            rings.append(((mm(t.GetWidth(pcbnew.F_Cu)) - d) / 2, "via"))
            drills.append(d)
    for f in board.GetFootprints():
        for p in f.Pads():
            if not p.HasHole():
                continue
            dx, dy = mm(p.GetDrillSizeX()), mm(p.GetDrillSizeY())
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                ring = min(mm(p.GetSizeX()) - dx, mm(p.GetSizeY()) - dy) / 2
                rings.append((ring, f"{f.GetReference()}.{p.GetNumber()}"))
                if abs(dx - dy) > 1e-3:
                    slots.append((min(dx, dy), f.GetReference()))
            drills.append(min(dx, dy))
    worst = min(rings)
    record(
        "fab",
        {
            "min_annular_mm": round(worst[0], 3),
            "at": worst[1],
            "min_drill_mm": min(drills),
            "plated_slots": sorted(set(slots)),
        },
    )
    assert worst[0] >= limits["min_annular"] - 1e-6
    assert min(drills) >= limits["min_drill"]
    assert all(w >= limits["min_plated_slot"] for w, _ in slots)


def test_design_rules_cover_fab_limits(
    board: Any, specs: ProjectModule, project: Project
) -> None:
    """Set the board's minimum track and clearance at the fab limit, and its copper."""
    limits = specs.PCBWAY
    ds = board.GetDesignSettings()
    # the stricter 2 oz limits, so either copper weight works
    assert mm(ds.m_TrackMinWidth) >= limits["min_track_2oz"]
    assert mm(ds.m_MinClearance) >= limits["min_space_2oz"]
    copper = project.config.stackup.copper_mm
    with open(board.GetFileName(), encoding="utf-8") as handle:
        text = handle.read()
    found = re.findall(
        r'\(layer "[FB]\.Cu"\s*\(type "copper"\)\s*\(thickness ([\d.]+)\)', text
    )
    assert found and all(abs(float(t) - copper) < 1e-6 for t in found), (
        f"stackup must say {copper:g} mm copper (pcbkit.toml [stackup] copper_mm)"
    )


def test_silkscreen_text_is_printable(
    board: Any, record: Any, specs: ProjectModule
) -> None:
    """Print no silkscreen text smaller or thinner than the fab house can."""
    pcbnew = _pcb.pcbnew()
    limits = specs.PCBWAY
    bad = []
    texts = [(t, "board") for t in board.Drawings() if t.GetClass() == "PCB_TEXT"]
    for f in board.GetFootprints():
        texts += [(f.Reference(), f.GetReference()), (f.Value(), f.GetReference())]
        texts += [
            (g, f.GetReference())
            for g in f.GraphicalItems()
            if g.GetClass() == "PCB_TEXT"
        ]
    for t, owner in texts:
        if t.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS) or not t.IsVisible():
            continue
        h, s = mm(t.GetTextSize().y), mm(t.GetTextThickness())
        if h < limits["silk_min_height"] - 1e-6 or s < limits["silk_min_stroke"] - 1e-6:
            bad.append((owner, t.GetText(), round(h, 2), round(s, 3)))
    record("silk below limits", len(bad))
    assert not bad, (
        f"height/stroke below {limits['silk_min_height']:.2f}/"
        f"{limits['silk_min_stroke']:.2f} mm: {bad[:8]}"
    )


def _visible_texts(board: Any, layer: int) -> list[tuple[str, str, tuple[float, ...]]]:
    """Return (owner, text, box) for every visible text on a layer, footprints' too."""
    out = []
    for f in board.GetFootprints():
        items = [f.Reference(), f.Value()] + [
            g for g in f.GraphicalItems() if g.GetClass() in ("PCB_TEXT", "FP_TEXT")
        ]
        for t in items:
            if t.GetLayer() != layer or not t.IsVisible():
                continue
            b = t.GetBoundingBox()
            x0, y0 = mm(b.GetX()) - OX, mm(b.GetY()) - OY
            box = (x0, y0, x0 + mm(b.GetWidth()), y0 + mm(b.GetHeight()))
            out.append((f.GetReference(), t.GetShownText(True), box))
    return out


def _overlap(a: Any, b: Any, gap: float = 0.0) -> bool:
    """Return True if two boxes (x0, y0, x1, y1) overlap, or come within ``gap``."""
    return (
        a[0] < b[2] + gap
        and b[0] < a[2] + gap
        and a[1] < b[3] + gap
        and b[1] < a[3] + gap
    )


def test_assembly_drawing_text_does_not_overlap(
    board: Any, record: Any, specs: ProjectModule
) -> None:
    """Keep every reference and pin name on the assembly drawing readable."""
    pcbnew = _pcb.pcbnew()
    exempt = set(getattr(specs, "ASSEMBLY_TEXT_EXEMPT", ()))
    texts = _visible_texts(board, pcbnew.F_Fab)
    bad = [
        (a[0] + ":" + a[1], b[0] + ":" + b[1])
        for i, a in enumerate(texts)
        for b in texts[i + 1 :]
        if _overlap(a[2], b[2])
    ]
    record("fab texts checked", len(texts))
    assert not bad, f"overlapping text on the assembly drawing: {bad[:8]}"
    # a designator printed over through-hole pins (drawn as filled drill dots) can't be
    # read either
    holes = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                c, d = p.GetPosition(), p.GetDrillSize()
                x0, y0 = mm(c.x - d.x // 2) - OX, mm(c.y - d.y // 2) - OY
                holes.append(
                    (
                        f.GetReference(),
                        p.GetNumber(),
                        (x0, y0, x0 + mm(d.x), y0 + mm(d.y)),
                    )
                )
    on_pins = sorted(
        {
            t[0] + ":" + t[1]
            for t in texts
            for h in holes
            if t[0] not in exempt and _overlap(t[2], h[2])
        }
    )
    assert not on_pins, (
        f"assembly-drawing text printed over through-hole pins: {on_pins[:8]}"
    )


def test_wire_pads_have_polarity_marks(
    board: Any, record: Any, specs: ProjectModule
) -> None:
    """Mark each wire pad with a + and a - on the silkscreen, by its own pad."""
    pcbnew = _pcb.pcbnew()
    marks = []
    for d in board.GetDrawings():
        text = d.GetText() if d.GetClass() == "PCB_TEXT" else None
        if text in ("+", "-") and d.GetLayer() == pcbnew.F_SilkS:
            p = d.GetPosition()
            marks.append((text, (mm(p.x) - OX, mm(p.y) - OY)))
    found = {}
    for ref, (plus, minus) in specs.WIRE_PADS.items():
        p1, p2 = xy(pad(board, ref, plus)), xy(pad(board, ref, minus))
        for sign, own, other in (("+", p1, p2), ("-", p2, p1)):
            ok = [
                m
                for t, m in marks
                if t == sign
                and math.dist(m, own) < 4.0
                and math.dist(m, own) < math.dist(m, other)
            ]
            found[f"{ref} {sign}"] = bool(ok)
    record("polarity marks", found)
    assert all(found.values()), (
        f"missing wire-pad polarity marks: {[k for k, v in found.items() if not v]}"
    )


# ------------------------------------------------------------------ placement quality
def test_decoupling_caps_are_close(
    board: Any, ic: str, pin: Any, caps: Any, limit: float, record: Any
) -> None:
    """Place a decoupling capacitor within its limit of the pin it serves."""
    px, py = xy(pad(board, ic, pin))
    parts = fps(board)
    d = min(
        math.hypot(px - xy(p)[0], py - xy(p)[1]) for c in caps for p in parts[c].Pads()
    )
    record(f"{ic}.{pin} nearest cap mm", round(d, 2))
    assert d <= limit


def test_power_footprints_match_datasheet(board: Any, specs: ProjectModule) -> None:
    """Match the pads of power parts to their datasheets: tab, sense pads, drills."""
    parts = fps(board)
    problems = []
    for rule in specs.POWER_FOOTPRINTS:
        ref = rule["ref"]
        footprint = parts[ref]
        if "tab_pad" in rule:
            pads = sorted(
                footprint.Pads(), key=lambda p: -mm(p.GetSizeX()) * mm(p.GetSizeY())
            )
            tab = pads[0]
            if tab.GetNumber() != str(rule["tab_pad"]):
                problems.append(
                    f"{ref}: the largest pad is {tab.GetNumber()}, "
                    f"the datasheet's tab is pad {rule['tab_pad']}"
                )
            net = rule.get("tab_net")
            if net is not None and tab.GetNetname() != "/" + net.lstrip("/"):
                problems.append(f"{ref}: the tab is on {tab.GetNetname()}, not {net}")
        if "smaller" in rule:
            area = {
                p.GetNumber(): mm(p.GetSizeX()) * mm(p.GetSizeY())
                for p in footprint.Pads()
            }
            for small, large in rule["smaller"]:
                if not area[str(small)] < area[str(large)]:
                    problems.append(
                        f"{ref}: pad {small} must be smaller than pad {large}"
                    )
        for num, want in rule.get("drill_mm", {}).items():
            got = mm(pad(board, ref, num).GetDrillSizeX())
            if abs(got - want) >= 0.01:
                problems.append(
                    f"{ref} pad {num}: drill {got:.2f} mm, datasheet {want} mm"
                )
    assert not problems, problems


def test_i2c_rise_time(
    board: Any, nl: Netlist, bus: str, record: Any, specs: ProjectModule
) -> None:
    """Keep each I2C line's rise time inside the bus speed's limit."""
    sda, scl, hz, offboard = specs.I2C_BUSES[bus]
    rail = getattr(specs, "I2C_PULLUP_NET", "+3V3")
    prefixes = getattr(specs, "I2C_DEVICE_PREFIXES", "UA")
    for line in (sda, scl):
        length = sum(
            mm(t.GetLength())
            for t in board.GetTracks()
            if t.GetClass() == "PCB_TRACK" and t.GetNetname() == "/" + line
        )
        ndev = len({r for r in nl.refs_on(line) if r[0] in prefixes}) + len(offboard)
        c = (
            ndev * specs.I2C_PIN_C
            + length * specs.TRACE_C_PER_MM
            + len(offboard) * specs.I2C_CABLE_LEN * specs.I2C_CABLE_C_PER_M
        )
        rp = next(
            parse_value(nl.parts[r]["value"])
            for r in nl.by_kind("Device:R")
            if {nl.net(r, 1), nl.net(r, 2)} == {line, rail}
        )
        tr = 0.8473 * rp * c
        record(
            line,
            {
                "trace_mm": round(length, 1),
                "c_pf": round(c * 1e12, 1),
                "rise_ns": round(tr * 1e9),
            },
        )
        limit = specs.I2C_TR_MAX[hz]
        assert tr <= limit, f"{line} rise {tr * 1e9:.0f} ns > {limit * 1e9:.0f} ns"
