# Worked examples: my-board

A generic board, made up to show the reasoning: a 24 V input (20 to 28 V) through a
10 A fuse F1, four DC gear motors on four 2-pin headers, a buck making 12 V, a buck
making 5 V for an off-board display, and a 3.3 V logic rail. Numbers marked
illustrative show the arithmetic and are not to be reused.

## 1. Display power through a 4-pin signal connector

- **Draft:** 5 V at 3 A to the display through a 4-pin 1.25 mm-pitch connector whose
  contacts are rated about 1 A each, two contacts on 5 V and two on GND.
- **Per contact:** 1.5 A each against about 1 A: 150 % of rating, `FAIL`. Judged per
  connector ("4 pins, 4 A") it looks fine, which is the trap.
- **Fix:** power on its own 2-pin connector rated 5 A per contact: 3 / 5 = 60 %, `OK`,
  provided the cable uses the wire gauge the rating assumes. Data on the 4-pin one, with
  its own ground contact so signal return does not ride the power ground.
- **Written:** `CONTACT_RATINGS` rows for both connectors (proposed table: `add-check`
  writes the check).

## 2. Motor stall on 2.54 mm headers

- **Stress:** stall assumed at 2.5 A per motor against header pins rated about 3 A: 83 %,
  over the 80 % target, `MARGINAL`, and the figure is `assumed`.
- **The finding is the assumption.** Stall current scales with the voltage across the
  winding: measure it at the rail's highest voltage, shaft locked, on a current-limited
  bench supply, for a second or two.
- **Fix limits:** the motor's own plug and lead are the same class of contact, so a
  better board-side header helps only if the plug changes too.

## 3. Four motors behind one 10 A fuse

- **Budget:** 4 x 2.5 A stall = 10 A behind a fuse that should carry no more than about
  75 % of 10 A after rerating.
- **Is simultaneous stall credible?** A jammed mechanism or a start from rest loads
  several motors at once. Nobody has said how many can stall together: `UNSTATED`, and
  the first finding. Decide it and write down who decided (firmware limit, staggered
  starts, a measured worst case).
- **Inrush:** illustrative: 28 V into 220 uF through 0.1 ohm in total gives I2t = V2C /
  (2R) = 28 x 28 x 220e-6 / 0.2 = about 0.86 A2s. Compare with the fuse's melting I2t
  after its pulse derating factor: `LOOKUP` in the fuse datasheet.
- **Written:** two `SUPPLY_LIMITS` rows, the budget and the I2t, and a `STRESS_SCENARIOS`
  row at 28 V with the motors stalled.

## 4. 10 uF 35 V ceramics on the 24 V input

- **Stress:** 28 V is 80 % of 35 V before any hot-plug ringing, which can approach twice
  the input undamped. `test_capacitor_voltage_derated` reports it at the edge once the
  28 V row is in `STRESS_SCENARIOS`; with a transient row it fails.
- **Real capacitance:** `LOOKUP`. Enter the exact part number in the maker's tool at
  28 V and the hottest ambient. Expect well under half; write no number until the tool
  gives one.
- **Fix options:** 50 V parts in a larger case, more in parallel, and a bulk polymer or
  electrolytic capacitor at the input whose ESR damps the ringing.

## 5. A 12 V rail from an input that sags

- **Low end:** at stall the input sags to 13 V at the buck. The buck runs at maximum
  duty and its output follows the input down, less the drop across the switch, the
  inductor, the fuse and the connector.
- **High end:** a 1 % reference with 1 % divider resistors can land the 12 V set-point
  near 12.4 V; compare that with each load's maximum.
- **Written:** a `SUPPLY_LIMITS` function row that reads the divider from the netlist,
  so a changed resistor is judged again:

```python
from pcbkit.check.netlist import parse_value


def divider_high(nl):
    """Return the 12 V rail's worst-case high against the motors' maximum."""
    top, bottom = (parse_value(nl.parts[r]["value"]) for r in ("R20", "R21"))
    v = 0.8 * 1.01 * (1 + top * 1.01 / (bottom * 0.99))  # U5 VREF 0.8 V, 1 %
    return ("12 V rail, worst case high (V)", round(v, 2), "<=", 12.6)


SUPPLY_LIMITS = [
    ("motors stalling together (A)", 4 * 2.5, "<=", 0.75 * 10),  # F1 rerating 75 %
    divider_high,
]
```

The first row fails until the simultaneous stall is settled: that is the point. It
stays red in `out/checks/results.json` until the design or a sourced decision changes it.
