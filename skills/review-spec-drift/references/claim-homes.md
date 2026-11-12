# Where each kind of claim lives

The drafts of this review kept checkable claims in a YAML file beside the spec. In
pcbkit the board project is that file: each claim a program can decide has a home that a
check reads. A claim marked "proposed" has a table but no built-in check yet: write the
rows and hand the table to `add-check`, which writes a project check that reads it
(`${CLAUDE_PLUGIN_ROOT}/plans/wp16-review-skills.md` lists them).

| Claim | Home | Check that holds it |
|---|---|---|
| Board outline size, corner radius | layout.py `W`, `H`, `CORNER_R`, `outline` | `test_outline_size` (`outputs`) |
| Mounting holes | layout.py `HOLES` | `test_drill_files_match_board` (`outputs`); a hole's drill in `POWER_FOOTPRINTS` `drill_mm` |
| Layers, thickness, copper weight | pcbkit.toml `[stackup]` | `test_job_file_stackup` (`outputs`) |
| Each part has an orderable part number | design.py `mpn` | `test_bom_complete` (`outputs`) |
| A part's pin functions | `PINOUT` | `test_symbol_pin_functions_match_datasheet` (`circuit`) |
| A rail's voltage at each load | `SUPPLY_SCENARIO`, `SUPPLY_PINS` | `test_supply_pins_in_range` (`circuit`) |
| A design range fits a limit (input range, a budget) | `SUPPLY_LIMITS` | `test_supply_pins_in_range` (`circuit`) |
| Bus devices and distinct addresses | `I2C_BUSES`, `circuits.i2c_devices` | `test_i2c_addresses_unique_per_bus` (`circuit`) |
| Current a path carries | `COPPER_SEGMENTS`, `COPPER_BUDGETS` | `test_copper_segment` (`copper`) |
| A region kept clear (antenna, keep-out) | `RESERVED_COPPER` | `test_reserved_copper_stays_clear` (`copper`) |
| ESP32-S3 DevKit header pins and analogue inputs | `DEVKIT_GPIO`, `ANALOG_NETS` | the `esp32s3` group |
| The net on each connector and controller pin; every connector in the port map | `PIN_NETS` (proposed) | none yet |
| Each port on its named edge | `PORT_EDGES` (proposed) | none yet |
| The exact addresses on a bus | `I2C_EXPECTED` (proposed) | none yet |
| What the board must not have | `EXCLUSIONS` (proposed) | none yet |
| "Source X powers Y only" | `POWER_PATHS` (proposed) | none yet |
| An enable or input's state when the controller is absent | `FAULT_STATES` (proposed, `walk-fault-states`) | none yet |
| Tallest part under a height | `HEIGHT_MAX_MM` (proposed) | none yet |
| Population variants | none: pcbkit has one population (`dnp`) | none |

Behaviour, intent and open questions stay in the prose spec.
