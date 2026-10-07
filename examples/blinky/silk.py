from __future__ import annotations

from typing import Any

from pcbkit.silk import bbox_of, iloc

# (text, x, y, height, rotation): x, y is the text's centre, in millimetres from the
# board's top-left corner.
LABELS = [
    ("BLINKY", 18.0, 4.0, 1.2, 0),
    ("VIN 3-5.5V", 14.0, 8.0, 0.9, 0),
]


def extra(board: Any, api: Any) -> None:
    """Keep text off the tracks, then print + by J1's VIN pin and - by its GND pin."""
    for item in board.GetTracks():
        if item.GetClass() == "PCB_TRACK":
            api.obstacles.add(*bbox_of(item))
    pads = {pad.GetNumber(): iloc(pad) for pad in api.footprints["J1"].Pads()}
    for number, mark, other in (("1", "+", "2"), ("2", "-", "1")):
        x, y = pads[number]
        ox, oy = pads[other]
        # away from the other pad, along the pad row, so the mark is nearest its own pad
        length = ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5
        ux, uy = (x - ox) / length, (y - oy) / length
        spots = [(x + ux * d, y + uy * d) for d in (3.2, 3.6, 2.8)]
        if not api.place(mark, spots, 1.0, bold=True):
            api.warn(f"no room for the {mark} mark of J1")
