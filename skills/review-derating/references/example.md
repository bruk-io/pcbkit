# Worked examples: my-board

A generic LED lighting driver, made up to show the reasoning: a 36 V input (30 to 42 V)
through a 5 A fuse F1 into three constant-current buck LED drivers, U3 to U5, one per
channel at 1.5 A into a string of about 24 V; a buck making 3.3 V for the controller;
the board sealed inside a luminaire housing. Numbers marked illustrative show the
reasoning and are not to be reused. Every stress below came from a check's record or a
solve of `circuits.scenario`, never from arithmetic in the conversation.

## 1. A common return through one contact

- **Draft:** the three strings share one return wire, which leaves through one contact
  of a 4-pin 2.0 mm-pitch wire-to-board header rated about 2 A per contact; the three
  channel outputs use the other three.
- **Per contact:** the return carries all three channels, 4.5 A, through one contact
  rated about 2 A: `FAIL`. Judged per connector ("4 pins at 2 A, 8 A") it looks fine,
  which is the trap; the failure is a return contact that runs hot and loosens.
- **Fix:** a connector whose return has as many contacts as the channels, or a
  terminal block rated for the total per position, with the wire gauge its rating
  assumes.
- **Written:** a `CONTACT_RATINGS` row for the header with `GND` at 4.5 A and each
  channel at 1.5 A (proposed table: `add-check` writes the check, which fails on the
  draft).

## 2. Inductor saturation at the housing's temperature

- **Stress:** peak inductor current is the 1.5 A load plus half the ripple at the
  highest input, 0.3 A illustrative, so 1.8 A. The inductor's Isat at 100 C is 1.9 A
  from its curve: 95 % of capability, `MARGINAL`.
- **The finding is the assumption.** The ambient inside the sealed housing is
  `assumed` at 60 C. Measure it: thermocouple on the inductor at full output for an
  hour, housing closed.
- **Written:** a `SUPPLY_LIMITS` row, U3's current limit against Isat at the hottest
  ambient, with the curve and its operating point beside it.

## 3. Three channels behind one 5 A fuse

- **Budget:** at the lowest input, all three channels at full draw about 3.9 A from the
  input (the solve at the `30 V, all channels on` scenario), against a fuse that should
  carry no more than 75 % of 5 A, 3.75 A, after rerating.
- **Is all-on credible?** Full white on every channel is the product's normal use, not
  a fault. Nobody has said whether firmware caps the total: `UNSTATED`, and the first
  finding. Decide it and write down who decided.
- **Inrush:** illustrative: 42 V into 100 uF through 0.15 ohm in total gives
  I2t = V2C / (2R), about 0.59 A2s; the real row is a `SUPPLY_LIMITS` function that
  sums the capacitance after F1 from the netlist (not shown). Compare with the fuse's melting I2t after its
  pulse derating factor: `LOOKUP` in the fuse datasheet.

## 4. 4.7 uF 50 V ceramics on the 42 V input

- **Stress:** `test_capacitor_voltage_derated` fails once the 42 V corner is in
  `STRESS_SCENARIOS`: 84 % of 50 V, before any hot-plug ringing on long supply leads.
- **Real capacitance:** `LOOKUP`. Enter the exact part number in the maker's tool at
  42 V and the hottest ambient; write no number until the tool gives one.
- **Fix options:** 100 V parts in a larger case, and a bulk electrolytic at the input
  whose ESR damps the ringing.

## 5. LED current set by a sense resistor

- **High end:** each driver sets its current as a sense threshold over a shunt. With a
  3 % threshold and a 1 % shunt, the worst-case current sits above nominal; compare it
  with the strip's maximum.
- **Written:** a `SUPPLY_LIMITS` function row that reads the shunt from the netlist, so
  a changed resistor is judged again:

```python
from pcbkit.check.netlist import parse_value


def led_current_high(nl):
    """Return channel 1's worst-case LED current against the strip's maximum."""
    shunt = parse_value(nl.parts["R11"]["value"])
    amps = 0.2 * 1.03 / (shunt * 0.99)  # U3 sense threshold 0.2 V +/-3 % (illustrative)
    return ("channel 1 LED current, worst case high (A)", round(amps, 2), "<=", 1.8)


SUPPLY_LIMITS = [
    # 3.9 A: the solve at the 30 V all-on scenario; F1 rerated to 75 %
    ("all channels on at 30 V, input current (A)", 3.9, "<=", 0.75 * 5),
    led_current_high,
]
```

The first row fails until the all-on question is settled: that is the point. It stays
red in `out/checks/results.json` until the design or a sourced decision changes it.
