# WP16: design-review skills

Status: in progress on branch `wp16-review-skills`. Seven review skills are in the
plugin; `review-datasheets` waits on WP14. No check is built here: the checks the
reviews found missing are listed at the end for `add-check` and the `check-writer`
agent.

## Goal

Give the plugin the judgement reviews a board needs between its gates (after the
schematic, after placement, before ordering), written for pcbkit: each review reads the
board project's own files and the results of `pcbkit check`, judges only what needs
intent, datasheets or physical reasoning, and ends by writing the intent it established
into `specs.py`, so the next run of the same question is a check, not a review.

## Decisions

- **In pcbkit, not a plugin of their own.** The reviews read pcbkit's files and write
  its tables; a separate plugin would have to describe pcbkit from outside.
- **Validation logic is code.** A review never asks a model to decide what the netlist or
  the geometry decides. Each skill opens with the checks that apply at its gate (a
  `pcbkit check -k` selection until WP17 adds a gate option) and tells Claude to read
  `out/checks/results.json` for them. Inside the review, decidable work comes first as
  table rows a check reads; the judgement (fit, consequence, remedy) follows the run.
- **Maintainer note: findings that recur.** When the same kind of finding turns up on a
  second board, the maintainers add a built-in check for it and take it out of the
  skill. The skill bodies do not say this: a session in a user's board project cannot
  edit the installed plugin.
- **Every review ends in `specs.py`.** The intent a review settles (which net is the
  victim, which loop is hot, what each enable must do with the controller dead, what a
  connector carries) goes into the tables the built-in checks read. Where no table
  exists yet, the review writes rows into a proposed table (marked so in a comment) and
  hands it to `add-check`, which writes a project check that reads it; the built-in
  check that replaces it is in "Checks to build" below. Every proposed table's row
  shape is in "Proposed tables" in `docs/project-interface.md`, which the skills point
  at; this plan is not read at run time.
- **Names.** Verb first and no `pcb-` prefix, like the skills already there:
  `walk-fault-states`, `review-derating`, `review-current-paths`, `review-misplug`,
  `plan-bringup`, `review-sourcing`, `review-spec-drift`, and later `review-datasheets`.
- **Inline, not forked.** Each review asks the user for intent and writes `specs.py`,
  so it must see the conversation and be able to ask. `review-board` stays forked: it
  needs neither.
- **Fixed output and gate kept.** Each review keeps the output format and the gate of
  its draft. The gates sit in the `board-workflow` loop:

  | Gate | After | Reviews |
  |---|---|---|
  | Schematic | `pcbkit sch` is ERC-clean | `walk-fault-states`, `review-derating`, `review-datasheets` (later) |
  | Placement | `pcbkit build`, before routing is final | `review-current-paths`, `review-misplug` |
  | Route | `pcbkit route`, before `promote` | `review-board` (as before) |
  | Order | `finalize` | `board-workflow` runs, in turn: `review-derating` if parts changed, `plan-bringup`, `review-sourcing`, `review-spec-drift`, a full `pcbkit check`; then the user's `/pcbkit:order-pcbway` |
  | Each gate with a written spec | | `review-spec-drift` |

  `board-workflow` alone owns the order intent; each review triggers on its own
  question. `review-sourcing` writes `sourcing.md` with the timestamps of the exports it
  read, and `order-pcbway` reads it.

- **Existing skills.** All five are kept; none is superseded.

  | Skill | Decision | Why |
  |---|---|---|
  | `board-workflow` | keep | The loop. It now says at which step each review runs. |
  | `new-board` | keep | Starting a board is not a review. It now hands the first build to the schematic-gate reviews. |
  | `add-check` | keep | Where every review sends a rule it found. It now says that a review hands over table rows, and that proposed tables are read by a project check until the built-in one exists. |
  | `review-board` | keep | A forked look at the routed copper from pictures, needing no intent: slivers, islands, signals under switchers, and a fast track over a gap in its reference pour (candidate 17, until check I). `review-current-paths` sets the intent (victims, hot loops, segments) the copper checks then measure; the two do different jobs. Its reply now names `review-current-paths` for a coupling no check measures and `add-check` for a finding a check could decide. |
  | `order-pcbway` | keep | Fills in the order. It now says that `review-sourcing` is what judges whether the parts can be bought. |

  The agents are kept as they are: the reviews dispatch `parts-researcher` for every
  datasheet figure, curve and stock level, and `check-writer` for every proposed check.

- **The content rule.** The drafts' worked examples came from one real board. Every
  example here is a generic board (`my-board`, plain references) teaching the same
  lesson; public datasheet facts stay as facts about the part.

## Candidate checks: triage

Every "candidate check" in the drafts, sorted. **Built**: a built-in check decides it
today (group, test, table). **Expressible**: rows in an existing table make a built-in
check decide it. **Missing**: no check can; the letter is its entry in "Checks to build".

| # | From | Candidate | Verdict | Where |
|---|---|---|---|---|
| 1 | fault states | Floating nets and unconnected pins | Built | `kicad`: `test_erc_clean` (an intended open pin has a no-connect flag in design.py) |
| 2 | fault states | ESP32-S3 strapping and input-only pins at reset | Built | `esp32s3`: `test_strapping_pins_at_reset` (`STRAP_SCENARIO`), `test_esp32_input_only_pins_have_a_defined_level` |
| 3 | fault states | Each enable, output-enable and stop input holds its safe level in each abnormal condition | Missing | A |
| 4 | fault states | Current injected into an unpowered board's pins by a separately powered device | Expressible (ESP32-S3) | `esp32s3`: `test_no_esp32_gpio_above_abs_max`, a `GPIO_SCENARIOS` row with the board's rail at 0 V |
| 5 | fault states | A source reaching a rail it must not (USB onto a load rail, back-feed through a diode or a signal pin) | Missing | B |
| 6 | fault states | Supply pins inside their datasheet range | Built | `circuit`: `test_supply_pins_in_range` (`SUPPLY_SCENARIO`, `SUPPLY_PINS`) |
| 7 | current paths | Track width against current, voltage drop | Built | `copper`: `test_copper_segment` (`COPPER_SEGMENTS`, `COPPER_BUDGETS`) |
| 8 | current paths | Clearances | Built | `kicad`: `test_drc_clean_with_schematic_parity` |
| 9 | current paths | Ground stitching | Built | `copper`: `test_stitching_density` (`[stitch]`, `STITCH_BASIS`) |
| 10 | current paths | Switch node, inductor or high-current copper beside a sensitive net | Built | `copper`: `test_parallel_length`, `test_sensitive_nets_exist` (`COUPLING`) |
| 11 | current paths | Filter capacitor at the pin it protects | Built | `copper`: `test_filter_caps_sit_by_the_adc_pin` (`COUPLING["filters"]`) |
| 12 | current paths | Kelvin sense pair encloses little area | Built | `copper`: `test_sense_pair_loop_area` (`SENSE_LOOPS`) |
| 13 | current paths | Decoupling and bulk capacitors at the pin they serve | Built | `fab`: `test_decoupling_caps_are_close` (`DECOUPLING`) |
| 14 | current paths | Region kept clear of copper (antenna, under an inductor) | Built | `copper`: `test_reserved_copper_stays_clear` (`RESERVED_COPPER`, plus the keep-out in routing.py) |
| 15 | current paths | Load return current lifting a sensor's ground | Expressible | `COPPER_SEGMENTS` row on `GND`, `heating` False, terminals at the load return and the sensor's ground pad; `SUPPLY_REGULATION` |
| 16 | current paths | Regulator hot-loop area | Missing | H (four-layer) |
| 17 | current paths | Fast net crossing a gap in its reference plane | Missing | I; `review-board` judges it by eye until then |
| 18 | current paths | Heat source beside a temperature-sensitive part | Missing | J |
| 19 | current paths | Connector return contacts as capable as its supply contacts | Missing | E |
| 20 | derating | Capacitor voltage under 80 % of rating, transients included | Built | `circuit`: `test_capacitor_voltage_derated` (`CAP_VRATED`, a transient is a `STRESS_SCENARIOS` row) |
| 21 | derating | FET and diode voltage ratings against the net's worst case | Missing | F |
| 22 | derating | Resistor dissipation | Built | `circuit`: `test_resistor_power_derated` (`RESISTOR_POWER_W`) |
| 23 | derating | Connector current per contact | Missing | E |
| 24 | derating | Load budget behind a shared connector or fuse | Expressible | `SUPPLY_LIMITS` row |
| 25 | derating | Feedback divider tolerance stack against the load's maximum | Expressible | `SUPPLY_LIMITS` row, a function of the netlist |
| 26 | derating | Inrush I²t into the capacitance behind a fuse | Expressible | `SUPPLY_LIMITS` row, a function of the netlist |
| 27 | derating | Inductor saturation against the regulator's current limit | Expressible | `SUPPLY_LIMITS` row |
| 28 | derating | Effective ceramic capacitance at DC bias against the regulator's minimum (once looked up) | Expressible | `SUPPLY_LIMITS` row, value from the maker's tool with its source |
| 29 | derating | Dropout at the lowest input | Expressible | `SUPPLY_LIMITS` row; `SUPPLY_PINS` |
| 30 | derating | LED current | Built | `circuit`: `test_led_currents` |
| 31 | derating | Copper heating at continuous current | Built | `copper`: `test_copper_segment` (`COPPER_BUDGETS` `i_cont`, `max_rise_c`) |
| 32 | mis-plug | Which net lands on which pin when a plug is reversed, offset or in the wrong port | Missing | D |
| 33 | mis-plug | Two user ports that accept the same plug | Missing | D |
| 34 | mis-plug | Adjacent pins at different voltages on a user-reachable connector | Missing | D |
| 35 | mis-plug | Polarity marks at wire pads | Built | `fab`: `test_wire_pads_have_polarity_marks` (`WIRE_PADS`) |
| 36 | mis-plug | Every connector labelled on the silkscreen | Missing | L |
| 37 | mis-plug | Current through a signal pin that becomes a plug's ground return | Expressible (ESP32-S3) | `esp32s3`: `test_esp32_gpio_source_current`, `test_no_esp32_gpio_above_abs_max`, a `GPIO_SCENARIOS` row for the reversed plug |
| 38 | mis-plug | An unplugged input reads its safe state | Missing | A |
| 39 | bring-up | Every rail has a labelled test point, a ground pad near it, reachable when assembled | Missing | G |
| 40 | bring-up | Each regulator on a shared enable can be isolated | Missing | S |
| 41 | bring-up | The addresses a bus scan should find | Built | `circuit`: `test_i2c_addresses_unique_per_bus` records each bus's table (`i2c` in results.json) |
| 42 | bring-up | The bus answers exactly the intended set | Missing | R |
| 43 | bring-up | Rail voltages and tolerances | Expressible | `SUPPLY_PINS`, `SUPPLY_LIMITS` |
| 44 | bring-up | Unsafe default state | Missing | A |
| 45 | sourcing | Every BOM line has an orderable part number | Built | `outputs`: `test_bom_complete` |
| 46 | sourcing | BOM, centroid and DNP agree with the board and schematic | Built | `outputs`: `test_bom_matches_schematic`, `test_centroid_matches_board` |
| 47 | sourcing | THT count, SMD placements, fine-pitch packages, unique lines | Built | `pcbkit quote`; it counts BGA, QFP and QFN by footprint name, not pitch, so DFN, LGA and wafer-level parts are listed from the BOM |
| 48 | sourcing | Fab parameters against the fab's limits | Built | `fab`: `test_annular_rings_and_drills_meet_pcbway`, `test_design_rules_cover_fab_limits` (`PCBWAY`); `outputs`: `test_job_file_stackup`; `pcbkit quote` |
| 49 | sourcing | Polarity or pin-1 mark visible outside the part body | Missing | K |
| 50 | sourcing | Fiducials | Missing | M |
| 51 | sourcing | Bottom-side parts flagged | Missing | N |
| 52 | sourcing | Footprint pads match the ordered part | Expressible (power parts) | `POWER_FOOTPRINTS`; the general case waits on WP14 part files |
| 53 | sourcing | Near-duplicate values | Missing | T |
| 54 | spec drift | Outline size and mounting holes | Built | `layout.W`, `H`, `HOLES` are the spec; `outputs`: `test_outline_size`, `test_drill_files_match_board` |
| 55 | spec drift | Symbol pin names match the datasheet | Built | `circuit`: `test_symbol_pin_functions_match_datasheet` (`PINOUT`) |
| 56 | spec drift | The net on each connector and controller pin | Missing | C (an ESP32-S3 DevKit board has `DEVKIT_GPIO`, `ANALOG_NETS`) |
| 57 | spec drift | Every connector is in the port map | Missing | C |
| 58 | spec drift | Each rail reaches its loads at its voltage | Expressible | `SUPPLY_PINS` rows per load |
| 59 | spec drift | Bus devices and unique addresses | Built | `I2C_BUSES`, `circuits.i2c_devices` |
| 60 | spec drift | Each port on its named edge | Missing | P |
| 61 | spec drift | Tallest part under a height limit | Missing | Q |
| 62 | spec drift | What the board must not have | Missing | O |
| 63 | spec drift | Population variants | Missing | U |
| 64 | spec drift | "Source X powers Y only" | Missing | B |

Totals: 64 candidates; **24 built, 12 expressible today, 28 missing** (21 checks to build,
some missing candidates sharing one).

## Checks to build

Not built in WP16. Each is one `check-writer` job; until it is built in, the review that
needs it writes rows into the proposed table and `add-check` writes a project check
that reads them. Ordered by what a miss costs.

| | Check | Table it reads (proposed) | Candidates | Notes |
|---|---|---|---|---|
| A | Each listed pin holds its window in each named condition | `FAULT_STATES = {condition: {"scenario": {kwargs for circuits.scenario}, "pins": [(ref, pin, (min V, max V))]}}` | 3, 38, 44 | A DC solve per condition, like `SUPPLY_PINS` but one scenario per condition. Covers missing pulls, pulls to an absent rail and unplugged inputs. |
| B | No DC path from one supply net to another except through the parts named as the way | `POWER_PATHS = [(from net, to net, (allowed refs))]` | 5, 64 | A graph walk over the netlist; diodes and FETs pass in their forward direction. Back-feed through an unpowered IC's ESD diodes needs a model of the part: later. |
| C | The net on each listed pad, and every connector listed | `PIN_NETS = {ref: {pad: net}}` | 56, 57 | The port map as the spec states it, compared with the netlist. |
| D | Reversed, offset and wrong-port mating lands nothing harmful | `MATING = {plug: {"pins": [net or None], "fits": [refs], "keyed": bool}}`; socket pad nets from the netlist | 32, 33, 34 | Pin by pin: supply onto ground or a signal, a higher rail onto a lower-rated pin, a safety loop closed. Pad adjacency from the footprint for tool shorts. |
| E | Current per contact against the derated contact rating | `CONTACT_RATINGS = {ref: {"amps": A per contact, "derate": fraction, "nets": {net: A}}}` | 19, 23 | Counts the contacts on each net from the netlist; return contacts judged like supply ones. |
| F | FET and diode voltage under 80 % of rating | `SEMI_VRATED = {mpn: {"vds" or "vrrm": V, "vgs": V}}` | 21 | The capacitor check's twin, on the same `STRESS_SCENARIOS`. |
| G | Each listed net has a test point, a ground pad within reach, outside the keep-away boxes | `TEST_POINTS = [(net, max ground mm)]`, `PROBE_KEEPAWAY = [(name, x0, y0, x1, y1)]` | 39 | Boxes are mated plug bodies and modules, in layout mm. |
| H | Regulator hot-loop area under a limit | `HOT_LOOPS = [{"name", "refs", "pads", "max_mm2"}]` | 16 | Waits on four-layer support (the loop closes through an inner plane). |
| I | A fast net never crosses a gap in its reference plane | `REFERENCE_PLANES = {net: layer}` | 17 | Buildable now: on two layers it is a top-layer track over the filled bottom `GND` zone, the gap a bottom track or slot. `review-board` judges it by eye until then. |
| J | Minimum distance between two parts | `PART_SPACING = [(ref, ref, min mm)]` | 18 | Heat sources from sensitive parts. |
| K | Polarity or pin-1 mark outside the part body | `POLARITY_MARKS = [refs]` | 49 | Generalises `WIRE_PADS`. |
| L | Every connector has a `CONN_LABELS` entry in silk.py | silk.py | 36 | |
| M | Fiducials present on each assembled side | `FIDUCIALS = {"min": n, "clearance_mm": mm}` | 50 | Only if the assembler asks for them. |
| N | No part on the bottom side unless listed | `BOTTOM_PARTS = [refs]` | 51 | |
| O | Absent refs and nets | `EXCLUSIONS = [(id, text, ref patterns, nets)]` | 62 | |
| P | Each port within reach of its edge | `PORT_EDGES = {ref: ("top" or "bottom" or "left" or "right", max mm)}` | 60 | |
| Q | Part height under a limit | `HEIGHT_MAX_MM` | 61 | Needs heights: WP14 part files. |
| R | A bus answers exactly the intended addresses | `I2C_EXPECTED = {bus: [addresses]}` | 42 | All-call and general-call addresses included. |
| S | Each regulator on a shared enable has its own link | `ENABLE_LINKS = {enable net: [regulator refs]}` | 40 | |
| T | Near-duplicate values | none: a report in `pcbkit quote` or the BOM | 53 | Lists candidates; never fails. |
| U | Population variants | none yet | 63 | pcbkit has one population; design first. |

## Later: `review-datasheets`

The eighth draft, `pcb-datasheet-review`, arrives after WP14 settles part files and
extraction. It then reads `parts/<part>.datasheet.json` and the WP14 part file for each
chip in `design.py`, and drops its own card format and its `extract_part_card.py` and
`verify_part_card.py`: `pcbkit datasheet find`, `confirm` and the WP14 `verify` do that
work in code. Its review is what stays judgement: what a floating or default pin does,
enable thresholds across the input range, required external parts and their maxima,
read at the cited page. Its candidates are already sorted above or in the tables:
absolute maximum against net voltage is `SUPPLY_PINS`; an all-call address is a device
for `I2C_BUSES` (the address check counts general-call ones); a pin's default state is A;
maximum output capacitance is a `SUPPLY_LIMITS` row. It writes `PINOUT`, `SUPPLY_PINS`,
`SUPPLY_LIMITS`, `POWER_FOOTPRINTS` and `DECOUPLING`, and runs at the schematic gate.

## Steps

1. This plan and the triage. Done.
2. The seven skills, one commit each, with generic worked examples. Done.
3. The existing skills hand off by name; `board-workflow` names the gates. Done.
4. `docs/claude-plugin.md`, the README line, and the plugin test's list of skills. Done.
5. Build the checks above, A to G first, with `add-check` and `check-writer`. Not
   started; the gate-scoped run and the rest move to WP17 (below).
5a. Audit fixes. Done: examples rebuilt around other products, gate-scoped `-k`
   selections in each review, decidable work before judgement, the proposed tables in
   `docs/project-interface.md`, one owner per trigger, `sourcing.md` for the order.
6. Eval cases for the reviews (`evals/`): at least that `walk-fault-states` loads for "is
   this fail-safe" and that a review reads results.json instead of re-measuring. Not
   started; the plugin test pins four cases today.
7. `review-datasheets`, after WP14.

## Left for WP17

What the skills work around today and WP17's pcbkit code replaces; each skill marks the
place with "WP17".

- `pcbkit check --gate sch|place|route|order`: replaces the `-k` selections in every
  review. Today: schematic `test_circuit or test_esp32s3 or test_erc_clean`; placement
  adds `test_decoupling_caps_are_close`, `test_power_footprints_match_datasheet` and
  `test_filter_caps_sit_by_the_adc_pin`; route `not test_outputs`; order, no `-k`.
- Built-in checks for the proposed tables, starting with A to G.
- Every part's stress at every `STRESS_SCENARIOS` row in results.json (today the
  capacitor and resistor checks record the top four, `test_supply_pins_in_range`
  nothing), and supply-pin windows at the envelope corners. `review-derating` solves
  the rest in a script meanwhile.
- A per-part derating factor, so a tantalum's 50 % target is a check, not a finding.
- A parts-stock command over `pcbkit/parts.py`, replacing `parts-researcher` for
  Mouser stock in `review-sourcing`.
- A ground-lift record per zero-share terminal in `test_copper_segment` (today only the
  segment's largest drop is recorded; `review-current-paths` asks `add-check` for it).
