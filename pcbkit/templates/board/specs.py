"""Datasheet numbers and design limits that Blinky's checks read.

Two readers: the project's own checks in checks/test_indicator.py (the first block), and
the built-in checks of the groups that pcbkit.toml switches on (the `fab` block). Each
number says where it comes from; change a part and change its number with it.
"""

from __future__ import annotations

# --- the supply and the LED: read by checks/test_indicator.py ----------------------

SUPPLY_NET = "VIN"
GROUND_NET = "GND"
# What VIN may be: a 3.3 V rail 10 % low up to a 5 V rail 10 % high.
SUPPLY_V = (3.0, 5.5)

# Kingbright APT1608SGC, datasheet DSAD0932 rev V.22B (2023-12-07), page 2.
# Forward voltage at 20 mA: 2.2 V typical, 2.5 V maximum. It gives no minimum, so the
# lowest voltage is the typical one less the +-0.1 V of the measurement and the 0.12 V
# the voltage falls by at 85 C (-2.0 mV/C), rounded down. A lower voltage means more
# current, so this is the safe end for the brightest case.
LED_VF_MAX = 2.5
LED_VF_MIN = 1.9
# Absolute maximum DC current is 25 mA, and the permitted current falls as the air
# warms (page 3): 20 mA, the datasheet's test current, leaves room for a warm room.
LED_I_MAX = 0.020
# The dimmest current that still reads as lit (the LED is 12 mcd typical at 20 mA).
LED_I_MIN = 0.001

# Yageo RC0603FR-07330RL: 0.1 W at 70 C. A resistor runs at no more than half of it.
RESISTOR_POWER_W = {"default": 0.1}

# --- fab: read by the built-in checks of the `fab` group ---------------------------

# PCBWay's capabilities page (https://www.pcbway.com/capabilities.html, read
# 2026-10-06). Track and spacing are the "Normal process" column of its outer layer
# table for 70 um (2 oz) copper, 7/8 mil: the stricter of the two weights, so the
# limits hold whichever you order. 8 mil is 0.2032 mm, which is more than the 0.2 mm
# that pcbkit starts from, so routing.py raises the board's clearance to cover it.
# The rest is from the page's main table: annular ring 0.15 mm, drills of 0.2 mm or
# more without a surcharge, plated slots of 0.5 mm or more, and legend at least
# 0.15 mm wide and 0.8 mm high.
PCBWAY = {
    "min_annular": 0.15,
    "min_drill": 0.2,
    "min_plated_slot": 0.5,
    "silk_min_height": 0.8,
    "silk_min_stroke": 0.15,
    "min_track_2oz": 0.1778,
    "min_space_2oz": 0.2032,
}

# J1 is a wire connector with polarity: pin 1 is VIN (+), pin 2 is GND (-). The check
# wants a + and a - on the silkscreen, each nearer its own pad (silk.py draws them).
WIRE_PADS = {"J1": ("1", "2")}

# JST XH datasheet (eXH.pdf), page 2: the 2-circuit top-entry header takes a 1.0 mm
# hole.
POWER_FOOTPRINTS = [{"ref": "J1", "drill_mm": {"1": 1.0, "2": 1.0}}]

# Rows of (ic, pin, (capacitor references), limit in mm) for the decoupling check, and
# the buses of the I2C check. Blinky has neither an IC nor a bus, so both are empty and
# the two checks that read them are skipped.
DECOUPLING: list = []
I2C_BUSES: dict = {}
