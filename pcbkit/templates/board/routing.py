from __future__ import annotations

from typing import Any

import layout

from pcbkit.kicad import board as kb

# Copper keeps this far apart (mm). PCBWay's 2 oz spacing is 8 mil, 0.2032 mm, a hair
# over the 0.2 mm that pcbkit starts from, so the rules are raised to cover it (specs.py
# holds the same limit, for the `fab` group).
CLEARANCE_MM = 0.21

# VIN gets a wider track than the default; the LED's 8 mA does not need it, but it shows
# how a net class is written: (track, clearance, via diameter, via drill, nets), in mm.
# A net name carries KiCad's leading "/" here and nowhere else.
NETCLASSES = {
    "Supply": (0.4, CLEARANCE_MM, 0.7, 0.3, ["/VIN"]),
}


def design_rules(ds: Any) -> None:
    """Raise the minimum clearance, and the default net class's, to CLEARANCE_MM."""
    ds.m_MinClearance = kb.mm(CLEARANCE_MM)
    ds.m_NetSettings.GetDefaultNetclass().SetClearance(kb.mm(CLEARANCE_MM))
    ds.m_NetSettings.RecomputeEffectiveNetclasses()


def prerouted(board: Any, api: Any) -> None:
    """Route nothing by hand: Freerouting joins VIN and LED_A, the pours carry GND."""


def zones(board: Any, api: Any) -> None:
    """Pour ground on both layers, a little inside the board edge."""
    import pcbnew

    outline = api.rect(0.3, 0.3, layout.W - 0.3, layout.H - 0.3)
    api.zone(board, "GND", pcbnew.B_Cu, outline)
    api.zone(board, "GND", pcbnew.F_Cu, outline)
