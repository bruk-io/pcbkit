# Worked example: my-board

A generic board, made up to show the walk. It sits in a printed case under a display
board on 12 mm standoffs, with its connectors on the edges. Pinouts are the ones in its
design.py.

## Connector inventory

| Ref | Family / pitch | Pins | Keying | Net and V per pin | What plugs in |
|---|---|---|---|---|---|
| J1 | 2-pin power housing, 5.0 mm | 2 | polarised | VIN 24 V, GND | the user's supply lead |
| J4 | family A, 2.0 mm | 2 | polarised housing | STOP_IN (pull-up to 3.3 V), GND; open = stop | normally-closed stop switch |
| J5 | family A, 2.0 mm | 2 | polarised housing | DOOR_IN (pull-up to 3.3 V), GND | normally-open door switch |
| J7 | family B, 1.25 mm | 3 | polarised, latch | GND, TX, RX | debug adapter |
| J8 | family B, 1.25 mm | 4 | polarised, latch | +5V, GND, SDA, SCL | third-party sensor module |
| J10-J13 | 2.54 mm header, unshrouded | 3 | none | GND, +5V, signal | 3-wire analogue sensors |

## Fit matrix (hazard cells only)

| Plug \ Socket | J4 | J5 | J7 | J8 |
|---|---|---|---|---|
| stop switch | mates | fits | no | no |
| door switch | fits | mates | no | no |
| debug adapter (3-pin) | no | no | mates | test (either end) |
| sensor module (4-pin) | no | no | no | mates |

Plus the header field: any sensor plug `fits` any of J10-J13 either way round and at an
offset. J1 is the only power housing on the board.

## Findings

| # | Connector | Misuse | Consequence | Suggested change |
|---|---|---|---|---|
| 1 | J4, J5 | door switch in J4 | A normally-open switch in a normally-closed loop: the stop reads open until the door closes, then the board runs whenever the door is shut. Safety defeat. | Design-out: put the stop on a 3-pin socket with the loop across pins 1 and 3, so no 2-pin plug at any offset closes it. |
| 2 | J4, J5 | stop switch in J5 | The door reads closed for ever. Silent failure. | Same change as 1. |
| 3 | J8 | debug adapter at pins 1-3 | Adapter GND on +5V, TX on GND, RX on SDA: the adapter's ground is driven to 5 V through the host's USB ground. Smoke, possibly in the host. | Design-out: make J7 4-pin with the same order as J8 and pin 1 unconnected, or move J7 to another family. Protection: a series resistor in TX. |
| 4 | J10-J13 | reversed | GND, +5V, signal puts power in the middle, so reversal swaps only ground and signal: the sensor's ground lands on the signal pin and its supply current returns through the ADC pin's protection diode. | Protection: a series resistor on each signal line, sized so that current stays under the pin's limit (`parts-researcher` for the figure). |
| 5 | J10-J13 | offset by one | Shifted toward the edge, the sensor's +5V wire lands on GND and its signal wire on +5V: 5 V into the sensor's output. | Design-out: shrouded 3-pin headers, or a case wall with one slot per plug. |
| 6 | J8 | third-party cable reversed end to end | The module's +5V pin lands on SCL; the board drives the unpowered module through its protection diodes. | Protection: series resistors on SDA and SCL; hand the back-feed to `walk-fault-states`. |

## Reachability

- [ ] each connector mateable with the display board fitted: open until the case model
  is checked against the display board's outline (is its edge set back enough for a
  thumb over each plug, and room above J7 and J8 to press the latches?)
- [ ] sensor leads clear the case lid hinge
- [ ] J4 and J5 labelled STOP and DOOR on silkscreen and case, not just their references
- [ ] reset and USB reachable with the case closed
- [ ] the supply lead anchored to the case, so J1 never carries its weight

Hand-offs: J4 unplugged must read stop (`walk-fault-states`: a `FAULT_STATES` condition);
J8 back-feed (`walk-fault-states`).

## Written to specs.py

```python
# Proposed tables: a project check from add-check reads them for now.
PIN_NETS = {
    "J4": {"1": "STOP_IN", "2": "GND"},
    "J5": {"1": "DOOR_IN", "2": "GND"},
    "J7": {"1": "GND", "2": "TX", "3": "RX"},
    "J8": {"1": "+5V", "2": "GND", "3": "SDA", "4": "SCL"},
}
MATING = {
    "debug adapter": {"pins": ["GND", "RX", "TX"], "fits": ["J7", "J8"], "keyed": True},
    "door switch": {"pins": ["SW", "SW"], "fits": ["J4", "J5"], "keyed": True},
}
```

silk.py gains `CONN_LABELS = {"J4": "STOP", "J5": "DOOR", ...}`.
