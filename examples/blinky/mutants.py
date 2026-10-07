"""Planted mistakes: each is a change to design.py that a check must catch.

`pcbkit mutants` makes a scratch copy of the project, applies the edits, rebuilds the
schematic and runs the checks that the pytest -k expression selects. The mistake is
caught when one of them fails. Each entry is (what is wrong, the edits to design.py as
(text to find, text to put there) pairs, the check that must fail).
"""

from __future__ import annotations

R1 = 'R("R1", "330", "VIN", "LED_A", B, "RC0603FR-07330RL"'
R1_AT_10_OHM = 'R("R1", "10", "VIN", "LED_A", B, "RC0603FR-0710RL"'

MUTANTS = [
    (
        "R1 is 10 ohm: the LED is overdriven",
        [(R1, R1_AT_10_OHM)],
        "test_led_current_window",
    ),
    (
        "R1 is 10 ohm: the resistor overheats",
        [(R1, R1_AT_10_OHM)],
        "test_resistor_power_margin",
    ),
    (
        "D1 is the wrong way round: the LED never lights",
        [('LED("D1", "Green", "LED_A", "GND"', 'LED("D1", "Green", "GND", "LED_A"')],
        "test_led_current_window",
    ),
]
