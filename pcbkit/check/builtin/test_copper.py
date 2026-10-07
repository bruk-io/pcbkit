"""Copper checks: reserved regions, ground stitching, coupling, current capacity.

Switched on by ``copper`` in ``[checks] groups``. They read the routed board, and from
the project's ``specs.py``:

* ``RESERVED_COPPER``: rows ``(name, layer, x0, y0, x1, y1, allowed nets)`` with the
  layer "top" or "bottom", the box in layout mm and the nets (KiCad's names, "/GND")
  that may put copper there. The router only keeps clear of a region if the project's
  keep-outs say so, so the routed copper is checked, not the intent.
* ``COUPLING``: what ``pcbkit.check.coupling.from_spec`` reads: ``classes`` (noise
  budget, victim source impedance, dI/dt per class), ``sensitive`` (nets per class),
  ``aggressors`` and any physical constant to change; and optionally ``filters``:
  ``{net: {"t_edge": seconds, "pin_ref": ref, "max_mm": mm}}`` for nodes with an RC
  filter downstream of the coupling, whose capacitor must sit near the pin it protects.
  Optional ``SENSE_LOOPS``: dicts with ``name``, ``nets`` (a pair), ``from_ref``,
  ``to_ref``, ``to_pads`` and ``max_mm2``: measurement pairs judged by the area they
  enclose.
* ``COPPER_SEGMENTS`` and ``COPPER_BUDGETS``: the high-current copper. Each segment has
  a ``name``, a ``net`` and ``terminals`` ``(ref, pad, share[, role])``: the share of
  the test current that enters the copper at that pad (negative: leaves), the last
  terminal being the 0 V reference; a segment with ``heating`` False is only judged on
  its voltage drop. ``COPPER_BUDGETS`` has
  ``i_cont`` (A, for heating), ``i_peak`` (A, for the drops), ``segment_drop_max_v`` and
  ``max_rise_c``. Optional ``SUPPLY_REGULATION``: ``supply`` and ``ground`` segment
  names, ``label``, ``nominal_v`` and ``max_fraction``, for the voltage the farthest
  load loses to supply sag and ground lift, found by the terminals' roles ("source" and
  "load").
* Optional: ``GROUND_NET`` ("GND") and ``STITCH_BASIS`` (``{"f_hz": ..., "er_fr4":
  ...}``, by default the top of the 2.4 GHz Wi-Fi band in FR4).

The copper thickness is ``[stackup] copper_mm``, the board thickness
``[stackup] thickness_mm`` and the stitching pitch ``[stitch] pitch_mm`` in pcbkit.toml.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pcbkit.check import _pcb, coupling
from pcbkit.check.copper import NetCopper, ipc2221_rise
from pcbkit.check.netlist import Netlist
from pcbkit.check.plugin import ProjectModule, spec_params
from pcbkit.check.reserved import intruders
from pcbkit.check.stitching import (
    Basis,
    far_patches,
    summarise,
    worst_locations,
)
from pcbkit.project import Project

mm = _pcb.to_mm


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the checks that run once per region, net, segment, loop or filter."""
    spec_params(
        metafunc,
        "name,layer,x0,y0,x1,y1,allowed",
        lambda specs: specs.RESERVED_COPPER,
        ids=lambda row: row[0],
    )
    spec_params(
        metafunc,
        "net",
        lambda specs: [
            coupling.slash(n)
            for nets in specs.COUPLING["sensitive"].values()
            for n in nets
        ],
    )
    spec_params(
        metafunc,
        "segment",
        lambda specs: list(specs.COPPER_SEGMENTS),
        ids=lambda segment: segment["name"],
    )
    spec_params(
        metafunc,
        "loop",
        lambda specs: list(getattr(specs, "SENSE_LOOPS", [])),
        ids=lambda loop: loop["name"],
    )
    spec_params(
        metafunc,
        "filtered",
        lambda specs: [coupling.slash(n) for n in specs.COUPLING.get("filters", {})],
    )


def _config(specs: ProjectModule, project: Project) -> coupling.Config:
    """Return the coupling configuration of the project's COUPLING table."""
    return coupling.from_spec(specs.COUPLING, project.config.stackup.thickness_mm)


def _ground(specs: ProjectModule) -> str:
    """Return the ground net's name as KiCad writes it."""
    return coupling.slash(getattr(specs, "GROUND_NET", "GND"))


# ------------------------------------------------------------------ reserved regions
def test_reserved_copper_stays_clear(
    board: Any,
    name: str,
    layer: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    allowed: Any,
    record: Any,
) -> None:
    """Keep every net but the allowed ones out of a region reserved for one job."""
    found = intruders(board, layer, (x0, y0, x1, y1), allowed)
    record(name, found)
    assert not found, found


# ------------------------------------------------------------------ ground stitching
def _basis(specs: ProjectModule, project: Project) -> Basis:
    """Return the basis of the stitching limits: pitch, frequency, permittivity."""
    return Basis(
        project.config.stitch.pitch_mm, **dict(getattr(specs, "STITCH_BASIS", {}))
    )


def test_stitching_basis(specs: ProjectModule, project: Project) -> None:
    """Choose a stitching pitch that can meet the RF limit by construction."""
    basis = _basis(specs, project)
    assert basis.rf_limit_mm > 0
    assert basis.grid_worst_mm <= basis.rf_limit_mm, (
        f"a {basis.pitch_mm:g} mm stitching grid leaves points "
        f"{basis.grid_worst_mm:.2f} mm from a via, over the {basis.rf_limit_mm:.2f} mm "
        "RF limit: lower [stitch] pitch_mm in pcbkit.toml"
    )


def test_stitching_density(
    board: Any, record: Any, specs: ProjectModule, project: Project
) -> None:
    """Stitch the two ground pours densely, leaving no large unstitched patch."""
    basis = _basis(specs, project)
    max_limit = basis.rf_limit_mm
    patch_limit = basis.rf_limit_mm
    p99_limit = basis.grid_worst_mm
    mx, p99, d, pts, order = summarise(board, _ground(specs))
    record("lambda_mm", round(basis.lambda_mm, 1))
    record("rf_limit_mm", round(basis.rf_limit_mm, 2))
    record("grid_worst_mm", round(basis.grid_worst_mm, 2))
    record("points_both_layers", len(d))
    record("max_dist_to_gnd_via_mm", round(mx, 2))
    record("p99_dist_to_gnd_via_mm", round(p99, 2))
    worst = worst_locations(d, pts, order, min(max_limit, p99_limit))
    record("worst_locations_layout_mm", worst)
    patches = far_patches(d, pts, max_limit)
    record("patches_beyond_lambda20 (extent, max dist, centre)", patches[:8])
    big = [p for p in patches if p[0] > patch_limit]
    assert not big and p99 <= p99_limit, (
        f"unstitched patches wider than {patch_limit:.2f} mm: {big[:6]}; "
        f"p99 {p99:.2f} mm (limit {p99_limit:.2f}); max {mx:.2f} mm"
    )


# ------------------------------------------------------------------ coupling
_analysis: dict[int, Any] = {}


def _result(board: Any, cfg: coupling.Config) -> Any:
    """Return the coupling analysis of the board, made once per run."""
    if id(board) not in _analysis:
        _analysis[id(board)] = coupling.analyse(board, cfg)
    return _analysis[id(board)]


def test_sensitive_nets_exist(
    board: Any, specs: ProjectModule, project: Project
) -> None:
    """Find tracks on every sensitive net, so a renamed net cannot slip through."""
    cfg = _config(specs, project)
    have = {t.GetNetname() for t in board.GetTracks()}
    missing = [n for n in cfg.all_sensitive if n not in have]
    assert not missing, f"sensitive nets with no tracks (renamed?): {missing}"


def test_lmax_basis() -> None:
    """Match the worked example: 10 mm at 4.1 mm centre distance is about 1.9 nH."""
    assert 1.7e-9 < coupling.mutual_inductance(10, 4.1) < 2.1e-9


def test_parallel_length(
    board: Any,
    nl: Netlist,
    net: str,
    record: Any,
    specs: ProjectModule,
    project: Project,
) -> None:
    """Run a sensitive net beside aggressor copper for less than its class allows."""
    cfg = _config(specs, project)
    res = _result(board, cfg)[net]
    credit: dict[str, float] = {}
    filters = {
        coupling.slash(n): f for n, f in specs.COUPLING.get("filters", {}).items()
    }
    if net in filters:
        att, tau = coupling.filter_credit(
            nl, net, filters[net]["t_edge"], _ground(specs).lstrip("/")
        )
        credit[net] = 1 / att
        record("filter", {"tau_ms": round(tau * 1e3, 3), "attenuation": att})
    record("D_near_mm", round(cfg.d_near, 2))
    for g, e in res.items():
        record(
            g,
            {
                "effective_mm": round(e["length"], 2),
                "raw_mm": round(e["raw"], 2),
                "lmax_mm": round(
                    coupling.limit(cfg, net, g, e, credit.get(net, 1.0)), 2
                ),
                "bbox_layout_mm": e["bbox"],
            },
        )
    bad = coupling.violations(cfg, {net: res}, credit)
    assert not bad, (
        f"{net} runs too long beside aggressor copper "
        f"(group, effective mm, Lmax mm, bbox x0 y0 x1 y1): {bad}"
    )


def test_filter_caps_sit_by_the_adc_pin(
    board: Any, nl: Netlist, filtered: str, record: Any, specs: ProjectModule
) -> None:
    """Put a filter capacitor by the pin it protects, as its credit assumes."""
    spec = {coupling.slash(n): f for n, f in specs.COUPLING["filters"].items()}[
        filtered
    ]
    name = filtered.lstrip("/")
    ground = _ground(specs).lstrip("/")
    caps = [
        c
        for c in nl.by_kind("Device:C", "Device:C_Polarized")
        if {nl.net(c, 1), nl.net(c, 2)} == {name, ground}
    ]
    assert caps, f"no filter capacitor on {name}"
    pins = [
        p
        for f in board.GetFootprints()
        if f.GetReference() == spec["pin_ref"]
        for p in f.Pads()
        if p.GetNetname() == filtered
    ]
    assert pins, f"{spec['pin_ref']} has no pad on {name} (COUPLING filters pin_ref)"
    pin = pins[0]
    ax, ay = mm(pin.GetX()), mm(pin.GetY())
    distances = [
        math.hypot(mm(p.GetX()) - ax, mm(p.GetY()) - ay)
        for f in board.GetFootprints()
        if f.GetReference() in caps
        for p in f.Pads()
        if p.GetNetname() == filtered
    ]
    assert distances, f"the filter capacitor {caps} has no pad on {name} on the board"
    d = min(distances)
    record("filter cap to ADC pin mm", round(d, 2))
    limit = spec["max_mm"]
    assert d <= limit, f"filter cap {d:.1f} mm from the ADC pin (limit {limit:.0f})"


def test_sense_pair_loop_area(board: Any, loop: dict[str, Any], record: Any) -> None:
    """Enclose little area between the two tracks of a measurement pair."""
    area, first, second = coupling.loop_area(
        board, tuple(loop["nets"]), loop["from_ref"], loop["to_ref"], loop["to_pads"]
    )
    limit = loop["max_mm2"]
    record(
        "sense loop",
        {
            "area_mm2": round(area, 1),
            "limit_mm2": round(limit, 1),
            "sense_p_mm": round(first, 1),
            "sense_n_mm": round(second, 1),
        },
    )
    first_net, second_net = loop["nets"]
    assert area <= limit, (
        f"{loop['name']} loop encloses {area:.0f} mm2 (limit {limit:.0f}): "
        f"route {first_net}/{second_net} as a pair"
    )


# ------------------------------------------------------------------ current capacity
_solved: dict[tuple[str, float], Any] = {}


def _terminals(nc: NetCopper, segment: dict[str, Any], amps: float) -> list[Any]:
    """Return the solver's terminals for a segment at a test current."""
    return [(nc.pad_cells(t[0], t[1]), t[2] * amps) for t in segment["terminals"]]


def _solve(board: Any, segment: dict[str, Any], amps: float, copper_mm: float) -> Any:
    """Solve a segment's copper at a test current (once per segment and current)."""
    key = (segment["name"], amps)
    if key not in _solved:
        nc = NetCopper(board, coupling.slash(segment["net"]), copper_mm=copper_mm)
        result = nc.solve(_terminals(nc, segment, amps))
        density = np.concatenate([nc.jw[layer][nc.masks[layer]] for layer in nc.layers])
        _solved[key] = (nc, result, density[density > 0])
    return _solved[key]


def test_copper_segment(
    board: Any,
    segment: dict[str, Any],
    record: Any,
    specs: ProjectModule,
    project: Project,
    out_dir: Path,
) -> None:
    """Carry the continuous current with a modest rise, the peak with a small drop."""
    budgets = specs.COPPER_BUDGETS
    i_cont, i_peak = budgets["i_cont"], budgets["i_peak"]
    copper_mm = project.config.stackup.copper_mm
    name, net = segment["name"], coupling.slash(segment["net"])
    nc, result, density = _solve(board, segment, i_cont, copper_mm)
    voltages = result["v_terminals"]
    drop = max(voltages) - min(voltages)
    # narrowest effective width: total current / 99.5th-percentile current-per-width
    j995 = float(np.percentile(density, 99.5))
    w_eff = i_cont / j995 if segment.get("heating", True) else None
    rise = ipc2221_rise(i_cont, w_eff, copper_mm) if w_eff else None
    amps = f"{i_cont:g}A"
    record(
        name,
        {
            f"drop_mV_at_{amps}": round(drop * 1e3, 1),
            f"loss_W_at_{amps}": round(result["loss_w"], 3),
            "peak_A_per_mm": round(j995, 2),
            "narrowest_equiv_width_mm": round(w_eff, 2) if w_eff else None,
            f"ipc2221_rise_C_at_{amps}": round(rise, 1) if rise else None,
            f"drop_mV_at_{i_peak:g}A": round(drop * 1e3 * i_peak / i_cont, 1),
        },
    )
    safe = name.replace(" ", "_").replace(">", "").replace("-", "")
    nc.heatmap(
        os.path.join(out_dir, f"copper_{safe}.png"),
        f"{name}  ({net.strip('/')}, {i_cont:.0f} A)",
    )
    # an external conductor carrying the full current should stay under the rise limit
    if rise is not None:
        assert rise <= budgets["max_rise_c"], (
            f"{name}: narrowest section ~{w_eff:.1f} mm -> {rise:.0f} C rise "
            f"at {i_cont:.0f} A"
        )
    peak_drop = drop * i_peak / i_cont
    assert peak_drop <= budgets["segment_drop_max_v"], (
        f"{name} drops {peak_drop * 1e3:.0f} mV at {i_peak:.0f} A"
    )


def test_load_supply_regulation(
    board: Any, record: Any, specs: ProjectModule, project: Project
) -> None:
    """Keep the farthest load's loss to supply sag and ground lift inside its budget."""
    if "SUPPLY_REGULATION" not in specs:
        pytest.skip(
            "specs.SUPPLY_REGULATION is not defined: no supply regulation to judge"
        )
    reg = specs.SUPPLY_REGULATION
    i_peak = specs.COPPER_BUDGETS["i_peak"]
    copper_mm = project.config.stackup.copper_mm
    by_name = {s["name"]: s for s in specs.COPPER_SEGMENTS}
    supply, ground = by_name[reg["supply"]], by_name[reg["ground"]]
    _, rs, _ = _solve(board, supply, i_peak, copper_mm)
    _, rg, _ = _solve(board, ground, i_peak, copper_mm)

    def role(segment: dict[str, Any], wanted: str) -> list[int]:
        return [
            i
            for i, t in enumerate(segment["terminals"])
            if len(t) > 3 and t[3] == wanted
        ]

    vs, vg = rs["v_terminals"], rg["v_terminals"]
    sources, sinks, loads = (
        role(supply, "source"),
        role(ground, "source"),
        role(ground, "load"),
    )
    assert len(sources) == 1, (
        f"{supply['name']} needs one terminal with the role 'source'"
    )
    assert len(sinks) == 1, (
        f"{ground['name']} needs one terminal with the role 'source'"
    )
    assert loads, f"{ground['name']} needs terminals with the role 'load'"
    source, sink = sources[0], sinks[0]
    sag_supply = vs[source] - min(vs[i] for i in range(len(vs)) if i != source)
    lift_ground = max(vg[i] for i in loads) - vg[sink]
    total = sag_supply + lift_ground
    nominal = reg["nominal_v"]
    record(
        f"{reg['label']} at {i_peak:g} A",
        {
            "supply_sag_mV": round(sag_supply * 1e3, 1),
            "ground_lift_mV": round(lift_ground * 1e3, 1),
            "total_mV": round(total * 1e3, 1),
            "pct_of_nominal": round(total / nominal * 100, 2),
        },
    )
    assert total <= reg["max_fraction"] * nominal, (
        f"{reg['label']} lose {total * 1e3:.0f} mV "
        f"(>{reg['max_fraction'] * 100:.0f}% of {nominal:g} V) at {i_peak:.0f} A"
    )
