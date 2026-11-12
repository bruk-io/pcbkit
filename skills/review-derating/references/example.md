# Worked examples: my-board

A generic USB-C hot plate for reflowing small boards, made up to show the reasoning. U1,
a USB-C power-delivery sink controller, asks the charger on J1 for 20 V at up to 5 A on
`VBUS`. Q1, an N-channel FET, switches a heating element on J2 with slow PWM. U2, a
linear regulator, makes 3.3 V from `VBUS` for U3, the controller, which reads a
thermistor. The board sits in the plate's base, under the heated surface. Numbers marked
illustrative show the reasoning and are not to be reused. Every stress and margin below
came from a check's record or from `stress.py`; every derived limit is a function row.

## 1. The heater through a small header

- **Draft:** the element plugs into J2, a 2-pin 2.54 mm wire-to-board header rated about
  3 A per contact (illustrative; the datasheet's figure at the base's ambient).
- **Per contact:** each of the two contacts carries the whole element current. A
  resistive element draws more cold than hot: the function row of section 5 solves it
  at about 6 A (illustrative), twice the contact rating. Judged by the running current
  with the plate hot, it looks fine, which is the trap.
- **Fix:** a connector rated for the cold current per contact, with the wire gauge its
  rating assumes, or two contacts per side if the cable has both wired.
- **Written:** a `CONTACT_RATINGS` row for J2 (proposed table: `add-check` writes the
  check, with the per-net currents at the top of its module, sourced, until the table
  says where they come from). It fails on the draft.

## 2. The FET against a hot-plug transient

- **Stress:** plugging a charged USB-C cable into ceramic input capacitance rings well
  above 20 V. With no measurement, the envelope takes the undamped case, about twice
  the supply, as a `STRESS_SCENARIOS` row (`vbus: 40.0, transient: True`, illustrative).
- **Result:** `stress.py` prints `Q1 vds` at 40 V against the 30 V rating in
  `SEMI_VRATED`: verdict `FAIL`. Q1 is fine at 20 V; it is the transient that fails it.
- **The finding is the assumption.** Measure the ring at J1 with the scope, or clamp it.
- **Fix:** a TVS at J1 rated for the input, and a 60 V FET.

## 3. Ceramics on a 20 V rail

- **Stress:** `test_capacitor_voltage_derated` fails once the transient row is in
  `STRESS_SCENARIOS`; at the steady 20 V row `stress.py` shows the 25 V parts at exactly
  the 80 % target, so the margin is gone even before any ringing.
- **Real capacitance:** `LOOKUP`. Enter the exact part number in the maker's tool at
  20 V and the hottest ambient; write no number until the tool gives one.
- **Fix:** 50 V parts in a larger case, and a bulk electrolytic at J1 whose ESR damps
  the ring.

## 4. A linear regulator in a hot base

The base is `assumed` at 70 C. U2 drops 20 V to 3.3 V, so its dissipation depends on the
controller's current, which comes from U3's datasheet, and its junction temperature on
the package's thermal resistance. That is arithmetic, so it is a function row, its
figures sourced beside it:

```python
def u2_junction(nl):
    """Return U2's junction temperature at the hottest base, against its limit."""
    vin, vout = 20.0, 3.3  # envelope: VBUS high; U2's output
    amps = 0.025  # U3 datasheet, typical active current (illustrative)
    theta_ja = 160.0  # U2 datasheet, C/W in its package (illustrative)
    t_j = 70.0 + (vin - vout) * amps * theta_ja
    return ("U2 junction at 70 C ambient (C)", round(t_j, 1), "<=", 125.0)
```

`stress.py` reports the row's value and margin. The finding is the 70 C assumption:
measure the base at full plate temperature before trusting the margin.

## 5. The input current against the charger's contract

The charger promises 5 A. The element's cold current at the top of the input range is
a solve, not a number to copy, so the row solves it:

```python
def element_current_cold(nl):
    """Return the element's current, cold, at 20 V, against the USB-C contract."""
    import circuits

    solved = circuits.scenario(nl, vbus=20.0, element="cold", duty=1.0).solve()
    amps = solved.resistor_current("element")  # the off-board element the scenario adds
    return ("element current, cold, 20 V (A)", round(amps, 2), "<=", 5.0)


SUPPLY_LIMITS = [u2_junction, element_current_cold]
```

The second row fails on the draft: the element must be chosen for the contract, or the
firmware must ramp the duty from cold, and the review says which, and who decided. It
stays red in `out/checks/results.json` until the design or a sourced decision changes
it.
