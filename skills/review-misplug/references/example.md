# Worked example: my-board

A generic irrigation controller, made up to show the review. It sits in a wall box
behind a hinged cover, its connectors along the bottom edge for wires that come up
from below. Pad nets are the ones in its netlist; U1, the controller, runs at 3.3 V.

## Connector inventory

| Ref | Family / pitch | Pins | Keying | Net and V per pin | What plugs in |
|---|---|---|---|---|---|
| J1 | pluggable terminal block, 5.08 mm | 2 | polarised housing | `VAC1`, `VAC2` (24 V AC) | the transformer's wires |
| J2 | pluggable terminal block, 5.08 mm | 7 | polarised housing | `VALVE1`-`VALVE6`, `VCOM` (24 V AC switched) | valve wires |
| J3 | pin header, 2.54 mm, unshrouded | 2 | none | `RAIN_IN` (pull-up to 3.3 V), `GND` | rain sensor, a dry contact |
| J4 | pin header, 2.54 mm, unshrouded | 3 | none | `+5V`, `GND`, `FLOW_IN` | flow meter (Hall sensor, 5 V) |
| J5 | RJ45 jack | 8 | latch | `RS485_A`, `RS485_B`, `GND` on pads 1, 2, 3; rest open | a second controller over RS-485 |

## Fit matrix (hazard cells only)

| Plug \ Socket | J3 | J4 | J5 |
|---|---|---|---|
| rain sensor plug (2-pin, 2.54 mm) | mates | fits (two offsets) | no |
| flow meter plug (3-pin, 2.54 mm) | fits (one wire overhanging) | mates | no |
| any RJ45 patch cable | no | no | mates |
| RJ45 cable from a PoE switch | no | no | fits |

J1 and J2 take 5.08 mm plugs only, and their housings stop reversal. The PoE cable is
meant for another device, so it has no intended landing and no `MATING` row: its cell
is judged below.

## Rows written, then checked

```python
# Proposed tables: checks/test_mating.py (from add-check) reads them.
PIN_NETS = {
    "J1": {1: "VAC1", 2: "VAC2"},
    "J2": {
        1: "VALVE1",
        2: "VALVE2",
        3: "VALVE3",
        4: "VALVE4",
        5: "VALVE5",
        6: "VALVE6",
        7: "VCOM",
    },
    "J3": {1: "RAIN_IN", 2: "GND"},
    "J4": {1: "+5V", 2: "GND", 3: "FLOW_IN"},
    "J5": {
        1: "RS485_A",
        2: "RS485_B",
        3: "GND",
        4: None,
        5: None,
        6: None,
        7: None,
        8: None,
    },
}
# U1 GPIO absolute maximum, 3.6 V (U1 datasheet); both inputs reach U1 directly.
NET_LIMITS = {"RAIN_IN": 3.6, "FLOW_IN": 3.6}
MATING = {
    "rain sensor": {"pins": ["RAIN_IN", "GND"], "fits": ["J3", "J4"], "keyed": False},
    "flow meter": {
        "pins": ["+5V", "GND", "FLOW_IN"],
        "fits": ["J4", "J3"],
        "keyed": False,
    },
}
```

By the `MATING` rule (wire k lands on pad k+offset; each plug straight and reversed, at
every offset up to one column overhanging either end), the check fails eight landings:

| Plug | Socket | Landing | What meets what |
|---|---|---|---|
| flow meter | J4 | reversed, offset 0 | its `+5V` wire on `FLOW_IN`, over the 3.6 V limit |
| flow meter | J4 | straight, offset +1 | its `+5V` wire on `GND`, a source fight |
| flow meter | J4 | straight, offset -1 | its `GND` wire on `+5V`, a source fight |
| flow meter | J4 | reversed, offset -1 | `GND` on `+5V` and `+5V` on `GND` |
| flow meter | J3 | straight, offset 0 | its `+5V` wire on `RAIN_IN`, over the limit |
| flow meter | J3 | reversed, offset -1 | its `+5V` wire on `GND` |
| rain sensor | J4 | straight, offset -1 | its `GND` wire on `+5V` |
| rain sensor | J4 | reversed, offset 0 | its `GND` wire on `+5V` |

The rain sensor passes every landing in J3 and the others in J4, and the check records
`+5V` beside `GND` on J4. What it cannot see: the rain sensor's two wires are joined by
its own contact, so in J4 at pads 1-2 (offset 0, which it passes) it shorts `+5V` to
`GND`. Nor does it see across connectors: `VAC2` on J1 sits 3 mm from `+5V` on J4.

## Findings

| # | Connector | Misuse | Consequence | Suggested change |
|---|---|---|---|---|
| 1 | J4 | flow meter plugged in reversed or one pin off | 5 V straight onto U1's `FLOW_IN` pin, or the meter's supply into ground. A damaged input, or a rail that collapses. | Design-out: a keyed 3-pin housing. Protection: a series resistor and clamp on `FLOW_IN`. |
| 2 | J4 | rain sensor plugged into the flow port at pads 1-2 | The closed contact shorts `+5V` to `GND`: the rail collapses and the controller resets in a loop. Smoke if the rail has no limit. | Design-out: the rain and flow ports in different families. Protection: a polyfuse on `+5V` to J4. |
| 3 | J3 | flow meter plugged into the rain port | 5 V onto `RAIN_IN`. | As finding 1: keyed housings of different pin counts. |
| 4 | J5 | a PoE patch cable plugged in | Up to 57 V onto the RS-485 transceiver. Smoke. | Design-out: a different connector for RS-485 (a 3-pin terminal block). Protection: TVS diodes rated for the PoE voltage. |
| 5 | J1, J4 | a stray strand of transformer wire bridging to the flow header | 24 V AC onto the 5 V rail. Smoke. | Design-out: J1 and J2 at the far end of the edge from J3 and J4, with a gap a strand cannot cross. |

## Reachability

- [ ] each connector mateable with the cover closed: open until the box model is
  checked (room below the edge for the plugs and a screwdriver?)
- [ ] wires leave through the box's glands without bending at the plugs
- [ ] J3 and J4 labelled RAIN and FLOW on silkscreen and inside the cover
- [ ] reset and the USB port reachable with the cover open
- [ ] the transformer leads clamped to the box, so J1 never carries their weight

Hand-offs: what watering does with J3 unplugged (`walk-fault-states`: a `FAULT_STATES`
condition); the flow meter hot-plugged into a live `+5V` (`walk-fault-states`).

## Written to specs.py

`PIN_NETS`, `NET_LIMITS` and `MATING` as above; silk.py gains `CONN_LABELS = {"J3":
"RAIN", "J4": "FLOW", ...}`.
