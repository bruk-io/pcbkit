# Worked example: my-board

A generic irrigation controller, made up to show the review. It sits in a wall box
behind a hinged cover, its connectors along the bottom edge for wires that come up
from below. Pad nets are the ones in its netlist.

## Connector inventory

| Ref | Family / pitch | Pins | Keying | Net and V per pin | What plugs in |
|---|---|---|---|---|---|
| J1 | pluggable terminal block, 5.08 mm | 2 | none | `VAC1`, `VAC2` (24 V AC) | the transformer's wires |
| J2 | pluggable terminal block, 5.08 mm | 7 | none | `VALVE1`-`VALVE6`, `VCOM` (24 V AC switched) | valve wires |
| J3 | pluggable terminal block, 3.5 mm | 2 | none | `RAIN_IN` (pull-up to 3.3 V), `GND` | rain sensor, a dry contact |
| J4 | pluggable terminal block, 3.5 mm | 3 | none | `+5V`, `GND`, `FLOW_IN` | flow meter (Hall sensor, 5 V) |
| J5 | RJ45 jack | 8 | latch | `RS485_A`, `RS485_B`, `GND` on pins 1, 2, 3; rest open | a second controller over RS-485 |

## Fit matrix (hazard cells only)

| Plug \ Socket | J3 | J4 | J5 |
|---|---|---|---|
| rain sensor plug (2-pin, 3.5 mm) | mates | fits (two offsets) | no |
| flow meter plug (3-pin, 3.5 mm) | no | mates | no |
| any RJ45 patch cable | no | no | mates |
| RJ45 cable from a PoE switch | no | no | fits |

J1 and J2 take 5.08 mm plugs only; no 3.5 mm plug seats in them.

## Rows written, then checked

```python
# Proposed table: checks/test_mating.py (from add-check) reads it, with each socket's
# pad nets from the netlist.
MATING = {
    "rain sensor": {"pins": ["SW", "SW"], "fits": ["J3", "J4"], "keyed": False},
    "flow meter": {"pins": ["+5V", "GND", "FLOW"], "fits": ["J4"], "keyed": False},
    "poe patch cable": {
        "pins": ["POE+", "POE+", "POE-", "DATA", "DATA", "POE-", "DATA", "DATA"],
        "fits": ["J5"],
        "keyed": True,
    },
}
```

The rain sensor's two wires are one closed contact, so both carry the same name. The
check failed three landings: the rain sensor in J4 at pins 1-2 (`+5V` onto `GND`
through the contact), the flow meter reversed in J4 (its supply wire on `FLOW_IN`, its
output on `+5V`), and the PoE cable in J5 (`POE+` on `RS485_A` and `RS485_B`, `POE-` on
`GND`). It also listed `VAC2` on J1 beside `+5V` on J4 across the 3 mm gap between the blocks.

## Findings

| # | Connector | Misuse | Consequence | Suggested change |
|---|---|---|---|---|
| 1 | J4 | rain sensor plugged into the flow port at pins 1-2 | The closed contact shorts `+5V` to `GND`: the rail collapses and the controller resets in a loop. Smoke if the rail has no limit. | Design-out: a 2-pin rain port and a 3-pin flow port in different pitches, or the flow port's supply on pin 2 so no 2-pin plug spans supply and ground. Protection: a polyfuse on `+5V` to J4. |
| 2 | J4 | flow meter plug inserted reversed (the block is unkeyed) | The meter is fed only through `FLOW_IN`'s pull-up and its output is tied to `+5V`: no pulses, and a push-pull output fights the rail. Silent failure or a dead meter. | Design-out: a keyed header. Label: pin 1 marked on case and silkscreen. |
| 3 | J5 | a PoE patch cable plugged in | Up to 57 V onto the RS-485 transceiver. Smoke. | Design-out: a different connector for RS-485 (a 3-pin terminal block). Protection: TVS diodes rated for the PoE voltage. |
| 4 | J1, J4 | a stray strand of transformer wire bridging to the flow block | 24 V AC onto the 5 V rail. Smoke. | Design-out: J1 and J2 at the far end of the edge from J3 and J4, with a gap a strand cannot cross. |

## Reachability

- [ ] each block mateable with the cover closed: open until the box model is checked
  (room below the edge for the plugs' screw heads and a screwdriver?)
- [ ] wires leave through the box's glands without bending at the plugs
- [ ] J3 and J4 labelled RAIN and FLOW on silkscreen and inside the cover
- [ ] reset and the USB port reachable with the cover open
- [ ] the transformer leads clamped to the box, so J1 never carries their weight

Hand-offs: what watering does with J3 unplugged (`walk-fault-states`: a `FAULT_STATES`
condition); the flow meter hot-plugged into a live `+5V` (`walk-fault-states`).

## Written to specs.py

`MATING` as above; silk.py gains `CONN_LABELS = {"J3": "RAIN", "J4": "FLOW", ...}`.
