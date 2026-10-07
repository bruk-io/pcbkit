"""Net classes and design rules, applied to a board before and after routing.

KiCad keeps design rules and net classes in the ``.kicad_pro`` beside a board, which a
copied ``.kicad_pcb`` does not carry, and the zone filler reads the rules compiled when
the board is loaded. So the router applies them from code, saves the board (which
writes the project file) and reloads it before it fills anything: a board rebuilt from
``golden/`` then fills exactly as the original run did. ``pre`` and ``post`` both call
``apply_rules``.

The numbers below are the fab house's limits (PCBWay, 1 oz; 2 oz is 6/6 mil) kept
at or above 0.2 mm, so either copper weight works. A project overrides any of them,
and adds its own, in its ``design_rules(ds)`` hook, which runs last. Its net classes
come from ``NETCLASSES``.
"""

from __future__ import annotations

from typing import Any

from pcbkit.kicad import board as kb
from pcbkit.route.hooks import Hooks

# The default net class: what a net gets when no pattern matches it.
DEFAULT_TRACK_MM = 0.25
DEFAULT_CLEARANCE_MM = 0.2
DEFAULT_VIA_MM = 0.7
DEFAULT_VIA_DRILL_MM = 0.3

# Design-rule minimums, in millimetres.
MIN_TRACK_MM = 0.2
MIN_CLEARANCE_MM = 0.2
MIN_VIA_MM = 0.6
MIN_THROUGH_DRILL_MM = 0.2
COPPER_EDGE_CLEARANCE_MM = 0.3
HOLE_CLEARANCE_MM = 0.25
HOLE_TO_HOLE_MM = 0.5
SOLDER_MASK_MIN_WIDTH_MM = 0.1
MIN_SILK_TEXT_HEIGHT_MM = 0.8
MIN_SILK_TEXT_THICKNESS_MM = 0.12


def apply_rules(board: Any, hooks: Hooks, copper_layers: int = 2) -> None:
    """Set the default net class, the project's net classes and the design rules.

    ``hooks.design_rules`` runs last with the board's design settings, so a project
    can change any of the minimums set here.
    """
    import pcbnew

    ds = board.GetDesignSettings()
    ns = ds.m_NetSettings
    default = ns.GetDefaultNetclass()
    default.SetTrackWidth(kb.mm(DEFAULT_TRACK_MM))
    default.SetClearance(kb.mm(DEFAULT_CLEARANCE_MM))
    default.SetViaDiameter(kb.mm(DEFAULT_VIA_MM))
    default.SetViaDrill(kb.mm(DEFAULT_VIA_DRILL_MM))
    ns.ClearNetclassPatternAssignments()
    for name, spec in hooks.netclasses.items():
        track, clearance, via_d, via_drill, patterns = spec
        netclass = pcbnew.NETCLASS(name)
        netclass.SetTrackWidth(kb.mm(track))
        netclass.SetClearance(kb.mm(clearance))
        netclass.SetViaDiameter(kb.mm(via_d))
        netclass.SetViaDrill(kb.mm(via_drill))
        netclass.SetPriority(1)
        ns.SetNetclass(name, netclass)
        for pattern in patterns:
            ns.SetNetclassPatternAssignment(pattern, name)
    ns.RecomputeEffectiveNetclasses()
    ds.m_TrackMinWidth = kb.mm(MIN_TRACK_MM)
    ds.m_MinClearance = kb.mm(MIN_CLEARANCE_MM)
    ds.m_ViasMinSize = kb.mm(MIN_VIA_MM)
    ds.m_MinThroughDrill = kb.mm(MIN_THROUGH_DRILL_MM)
    ds.m_CopperEdgeClearance = kb.mm(COPPER_EDGE_CLEARANCE_MM)
    ds.m_HoleClearance = kb.mm(HOLE_CLEARANCE_MM)
    ds.m_HoleToHoleMin = kb.mm(HOLE_TO_HOLE_MM)
    ds.m_SolderMaskMinWidth = kb.mm(SOLDER_MASK_MIN_WIDTH_MM)
    ds.m_MinSilkTextHeight = kb.mm(MIN_SILK_TEXT_HEIGHT_MM)
    ds.m_MinSilkTextThickness = kb.mm(MIN_SILK_TEXT_THICKNESS_MM)
    ds.SetCopperLayerCount(copper_layers)
    if hooks.design_rules is not None:
        hooks.design_rules(ds)
