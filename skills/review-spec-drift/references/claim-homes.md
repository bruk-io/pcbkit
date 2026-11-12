# Where each kind of claim is written down

Each claim a program can decide is written down, as the spec states it, where code
compares it with the design: a `specs.py` table a check reads, or a key of
`spec-claims.json` that `listing.py` compares with its listing. A table marked
"proposed" has no built-in check yet: write the rows and hand the table to `add-check`,
which writes a project check that reads it. Their shapes are in "Proposed tables" in
`${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`.

| Claim | Written in | Compared by |
|---|---|---|
| Board outline size | `spec-claims.json`: `outline.W`, `outline.H` | `listing.py` against layout.py `W`, `H`; `test_outline_size` (`outputs`) holds the fab files to them |
| Number of mounting holes | `spec-claims.json`: `holes` | `listing.py` against layout.py `HOLES` |
| Layers, thickness, copper weight | `spec-claims.json`: `stackup.layers`, `stackup.thickness_mm`, `stackup.copper_mm` | `listing.py` against `[stackup]`; `test_job_file_stackup` (`outputs`) holds the fab files to it |
| Which parts of a kind the board has (connectors, ICs, switches, jumpers, test points) | `spec-claims.json`: `parts.<category>` | `listing.py` |
| A part's footprint, value, MPN, pad count, or that it is left unfitted | `spec-claims.json`: `part.<ref>.<field>` | `listing.py` |
| The pads a net reaches | `spec-claims.json`: `net.<name>` | `listing.py` |
| Corner radius, outline shape | none | compare by eye in `pcbkit shots` |
| Each part has an orderable part number | design.py `mpn` | `test_bom_complete` (`outputs`) |
| A part's pin functions | `PINOUT` | `test_symbol_pin_functions_match_datasheet` (`circuit`) |
| A rail's voltage at each load | `SUPPLY_SCENARIO`, `SUPPLY_PINS` | `test_supply_pins_in_range` (`circuit`) |
| A design range fits a limit (input range, a budget) | `SUPPLY_LIMITS` | `test_supply_pins_in_range` (`circuit`) |
| Bus devices and distinct addresses | `I2C_BUSES`, `circuits.i2c_devices` | `test_i2c_addresses_unique_per_bus` (`circuit`) |
| Current a path carries | `COPPER_SEGMENTS`, `COPPER_BUDGETS` | `test_copper_segment` (`copper`) |
| A region kept clear (antenna, keep-out) | `RESERVED_COPPER` | `test_reserved_copper_stays_clear` (`copper`) |
| ESP32-S3 DevKit header pins and analogue inputs | `DEVKIT_GPIO`, `ANALOG_NETS` | the `esp32s3` group |
| The net on each connector and controller pad; every connector in the port map | `PIN_NETS` (proposed) | its project check |
| Each port on its named edge | `PORT_EDGES` (proposed) | its project check |
| The exact addresses on a bus | `I2C_EXPECTED` (proposed) | its project check |
| What the board must not have | `EXCLUSIONS` (proposed) | its project check |
| "Source X powers Y only" | `POWER_PATHS` (proposed) | its project check |
| An enable or input's state when the controller is absent | `FAULT_STATES` (proposed, `walk-fault-states`) | its project check |
| Tallest part under a height | `HEIGHT_MAX_MM` (proposed) | nothing yet: heights arrive with part files, so it stays `cannot tell` |
| Population variants | none: pcbkit has one population (`dnp`) | none |

Behaviour, intent and open questions stay in the prose spec.
