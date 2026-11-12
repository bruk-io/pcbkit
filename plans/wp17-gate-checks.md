# WP17: gate-scoped checks and the netlist checks the reviews need

Status: planned on branch `wp17-gate-checks`, cut from `wp16-review-skills` at 9bdb98a. Nothing built yet. This plan splits the brief in two. WP17 covers the gated check run, the results records, the derating factor, the table validator, `pcbkit parts stock`, and every check that reads only the netlist: A, B, C, D, F, R and S. WP18 covers the checks that read the board: G and the two-layer I first, then J, M, N, P, K, E, L and O, plus the neck and sliver rule. The reason for the split is that every WP17 slice can be judged on a machine with no KiCad, and almost every WP18 slice can only be judged on the owner's Mac. WP18 is sketched at the end, so it can be filed as its own plan.

## Goal

A review run at a design gate should start from code, not from a walk by hand. The gate's own check run is clean once that gate's inputs exist. `results.json` holds every number a review would otherwise work out itself. Every table a review writes into `specs.py` has one shape, defined in one place, and is validated by code. Validation logic is a script, never an instruction to a model.

## Where the codebase differs from the brief

The codebase wins in each case. Each one changes something below.

1. **There is no `pcbkit parts` command.** WP14 planned `pcbkit parts lookup`, but `pcbkit/cli.py` has only `datasheet find` and `confirm`. `pcbkit/parts.py` is the cache-plus-Mouser layer (`ask_mouser`, `STOCK_MAX_AGE`). `parts stock` therefore creates the `parts` group.
2. **The proposed table shapes are not in `docs/project-interface.md`.** Both `wp16-review-skills` and this branch are at 9bdb98a, and neither has them. They exist only in the skills (`skills/*/SKILL.md`, `references/example.md`), in `docs/claude-plugin.md` and in `plans/wp16-review-skills.md`. Slice 2 gives them their home.
3. **More than the unit tests runs without KiCad.** `tests/integration/test_check_plugin.py` has no `kicad` mark. It runs the real `runner.command` in a child pytest against the generic toy board (`tests/check_toy.py`), and `-p fake_nl` serves the netlist. Every netlist check can therefore be proved end to end here. Slice 1 confirms that it runs (`uv run pytest tests/integration/test_check_plugin.py -q`). If it does not, the netlist slices fall back to engine unit tests plus a Mac run.
4. **The schematic gate needs no pcbnew.** `pcbkit check` is tier 2, but the `nl` fixture uses only kicad-cli (`fresh_netlist`), and so do `test_erc_clean` and `test_bom_complete`. A `--gate sch` run can be tier 1, like `pcbkit sch`.
5. **Pins are named in two ways.** The `walk-fault-states` example writes `FAULT_STATES` pins by name (`("U2", "EN", ...)`). `SUPPLY_PINS` and `Netlist.net(ref, pin)` take pad numbers.
6. **WP16's `ENABLE_LINKS` shape (`{enable net: [regulator refs]}`) has no pad,** so a check cannot find the regulator's enable pin.
7. **`dc.build` models only some parts.** It covers `Device:R`, `R_Pack04`, `R_Shunt`, `LED`, `D_Schottky`, `Q_PMOS_GDS`, `AO3401A` and `2N7002`. It does not model `Device:D`, Zeners, generic FETs, BJTs or any IC.
8. **Islands are already removed.** `pcbkit/route/post.py:187` does the final fill with `ISLAND_REMOVAL_MODE_ALWAYS`, so isolated copper cannot survive `post`. What `review-board` judges by eye that no rule holds today is necks and slivers (item 7).
9. **The design rules are applied by `pre` and `post`, not by `pcbkit build`** (`pcbkit/route/rules.py` docstring). So `test_design_rules_cover_fab_limits` belongs to the route gate, not the placement gate. The silk checks (`test_silkscreen_text_is_printable`, `test_wire_pads_have_polarity_marks`, `test_assembly_drawing_text_does_not_overlap`) are route-gate too, because `apply_silk` runs in `route` and `finalize`.
10. **`test_capacitor_voltage_derated` records nothing when it fails.** Its reverse-bias and missing-rating asserts sit inside the loop, before `record`.
11. **`tests/unit/test_plugin.py::command_problems` does not look inside groups.** A skill that writes `pcbkit parts stock --qty 10` would be reported as "`pcbkit parts` has no option --qty". Slice 5 fixes the scan.
12. **wp15 also edits `pcbkit/check/plugin.py`.** Slice 6 of `plans/wp15-four-layer.md` changes the results block at line 469 (`copper_mm`), and WP17's slice 1 edits the same function.

## Decisions

### D1. Gates: derived from fixtures, with a central exception table and a marker (gate names are the owner's call)

Options weighed:

| Option | For | Against |
|---|---|---|
| Gate per group | One line per group | Too coarse. `kicad` holds ERC (schematic gate) and DRC (route gate). `fab` holds decoupling (placement gate) and via rings (route gate). |
| Marker on every check | Exact | Every built-in module gets edited, including `test_copper.py`, `test_fab.py` and `test_outputs.py`, which wp15 is changing. Project checks without a marker drift. |
| Derived from fixtures | Automatic. Project checks get it free. | Cannot tell placement from routing (both ask for `board`), and misses checks that read files through `project` (DRC). |

**Recommendation: derive, then apply exceptions, then the marker.**

- Gates are ordered `sch` < `place` < `route` < `order`. A check's gate is the earliest gate at which its inputs exist.
- The derived gate comes from `item.fixturenames`, which pytest closes over fixtures that request other fixtures:
  - `fab_dir` in it: `order`;
  - otherwise `board`: `route`, the conservative choice;
  - otherwise: `sch`.
- `BUILTIN_GATES` in `pcbkit/check/results.py` holds the built-in exceptions, keyed `module::function`:
  - `test_kicad::test_drc_clean_with_schematic_parity`: `route`. It reads the board through `project`, not `board`.
  - `test_fab::test_decoupling_caps_are_close`: `place`.
  - `test_fab::test_power_footprints_match_datasheet`: `place`.
- `@pytest.mark.pcbkit_gate("place")` overrides both, for project checks and new modules. The plugin registers the marker in `pytest_configure`.
- None of the files wp15 is changing is edited.

Running a check later than it could run is safe, because the full run before ordering catches it. Running it too early fails loudly. That asymmetry is why `board` defaults to `route`.

How the gated run works:

- `pcbkit check --gate G` passes `--pcbkit-gate G` to the plugin.
- `pytest_collection_modifyitems` deselects every check whose gate is later than G.
- Those checks are counted apart from `-k` deselections.

`results.json` gains these fields (additive, so `RESULTS_FORMAT` stays 1):

- `"gate"`: `"sch"`, `"place"`, `"route"`, `"order"` or null for a run with no gate.
- `selection.gated_out`: how many checks were held back.
- `"waiting"`: `{check id: gate}` for the checks held back.
- `"gate"` on each entry.

`selection.complete` is false for any gate before `order`. So `--gate order` is a full run, and the order skill's preflight still refuses a partial run.

**Owner's call:** that `pcbkit check --gate sch` skips `require_pcbnew()` and runs anywhere kicad-cli is installed. Recommended, because the schematic-gate reviews then run before any venv is built on KiCad's Python. The "Two tiers" line in CLAUDE.md changes with it.

Gates of the existing checks, which a unit test freezes:

| Gate | Checks |
|---|---|
| sch | `test_erc_clean`, `test_bom_complete`, `test_stitching_basis`, `test_lmax_basis`, all of `circuit` and `esp32s3` |
| place | `test_decoupling_caps_are_close`, `test_power_footprints_match_datasheet` |
| route | the rest of `fab`, all `copper` checks that read the board, `test_drc_clean_with_schematic_parity` |
| order | every `outputs` check except `test_bom_complete` |

### D2. New checks go in new opt-in groups, not the existing ones (group names are the owner's call)

A new check in `circuit` would make every board that has `circuit` switched on fail with "specs.FAULT_STATES is not defined" when pcbkit is upgraded. The existing rule, that a missing table fails by name, is right once a group is chosen, so new tables come with new groups. A board writes `{}` for "none here", as it does for `WIRE_PADS`.

`BUILTIN_GROUPS` already maps module to group, so each check gets its own module file. That keeps the slices parallel.

| Group | Modules (checks) | WP |
|---|---|---|
| `power` | `test_faults.py` (A), `test_power_paths.py` (B), `test_enable_links.py` (S), `test_semis.py` (F) | 17 |
| `ports` | `test_ports.py` (C), `test_mating.py` (D) | 17 |
| `bringup` | `test_i2c_expected.py` (R); `test_test_points.py` (G) in WP18 | 17, 18 |
| `planes` | `test_reference_planes.py` (I, two layers) | 18 |
| `placement` | J, M, N, P | 18 |

### D3. Tables: one validator, the shapes as data in code, the docs held to them

Options weighed:

- A hand-written check per table: what exists now. It has no common messages, and a mistyped row raises a bare `ValueError` deep in a check.
- pydantic or similar: a new dependency in KiCad's Python 3.9 venv. No.
- TypedDicts: no runtime checking on 3.9.
- **A small shape language in a new `pcbkit/check/tables.py`.** Recommended.

How it works:

- The shapes are plain data built from `Text`, `Net`, `Ref`, `Pad` (an int or a str, normalised to str), `Volts`, `Ohms`, `Mm`, `Fraction`, `Window` (min ≤ max), `Box` (x0 < x1, y0 < y1), `Opt`, `Tuple`, `ListOf`, `DictOf`, `Record(required, optional)`, `OneOf` and `Callable`.
- `validate(name, value, shape)` returns every error with its path, for example: `specs.FAULT_STATES["logic_absent"]["pins"][1]: expected (ref, pad, (min V, max V)), got ('U2', 'EN')`.
- `describe(shape)` renders the shape as text.
- `read(specs, name)` returns the validated value or raises `TableError`. `spec_params` (plugin) catches `TableError` the way it catches `MissingSpec`, so a malformed table fails its check by name instead of erroring collection.
- One home: each engine module declares `TABLES = {name: shape}`, and `tables.registry()` gathers them by walking `pcbkit.check` with `pkgutil`, so parallel slices share no registry file. The existing tables' shapes live in `tables.py`, so `copper.py`, `coupling.py` and the rest (wp15's files) are not touched.
- A doc test holds every registered table to a row in the checks section that contains `describe(shape)` word for word.

Existing tables get shapes too, but in WP17 they are validated only by a new tier-1 command, `pcbkit specs` (**owner's call on the name**). It imports `specs.py`, validates every registered table it defines, prints one line per error and exits 1 if there is any. The existing checks keep reading their tables exactly as now until M7 shows that no working board is refused. Wiring `tables.read` into them is a WP18 slice. Shapes are as permissive as the checks are today: a pad as int or str, a `SUPPLY_LIMITS` row as a tuple or a function. `COUPLING` stays with `coupling.from_spec`, which already validates it.

Proposed tables are registered as soon as the owner signs off their shapes, even before their checks exist. A review that writes rows can then run `pcbkit specs` straight away.

### D4. The `specs.py` shape sheet (all of it is the owner's call)

Pads are pad numbers as the netlist writes them, the same as `SUPPLY_PINS`, never pin names. The `walk-fault-states` example changes to match (**owner's call**: the alternative is to resolve a name through the netlist pin function, failing when more than one pad has it).

| Table | Shape | Check | Changed from WP16 |
|---|---|---|---|
| `CAP_DERATE` | `{mpn: fraction}`, each in (0, 0.8] | caps (circuit) | new |
| `FAULT_STATES` | `{condition: {"scenario": {keyword: value}, "pins": [(ref, pad, (min V, max V))]}}` | A | pads, not names |
| `POWER_PATHS` | `{"nets": [net], "paths": [(from net, to net, (refs))], "parts": {ref: [(in pad, out pad)]}, "max_ohm": ohm (optional, default 10)}` | B | was `[(from, to, (refs))]` |
| `PIN_NETS` | `{ref: {pad: net or None}}` | C, D | as WP16 |
| `MATING` | `{plug: {"pins": [net or None], "fits": [ref], "keyed": bool, "rows": 1 or 2 (optional)}}` | D | `rows` added |
| `NET_LIMITS` | `{net: max V}`, optional | D | new |
| `SEMI_VRATED` | `{mpn: {"vds": V, "vgs": V} or {"vrrm": V}, optional "derate": fraction ≤ 0.8, optional "pins": {"g", "d", "s"} or {"a", "k"} to pads}` | F | `derate`, `pins` added |
| `I2C_EXPECTED` | `{bus: [7-bit address]}`, buses a subset of `I2C_BUSES` | R | as WP16 |
| `ENABLE_LINKS` | `{enable net: [(regulator ref, enable pad)]}` | S | pad added |
| `TEST_POINTS` | `[(net, max ground mm)]` | G (WP18) | as WP16 |
| `PROBE_KEEPAWAY` | `[(name, x0, y0, x1, y1)]` in layout mm | G | as WP16 |
| `REFERENCE_PLANES` | `{net: "top" or "bottom" or ("top" or "bottom", max unreferenced mm)}` | I (WP18) | tuple form added |
| `PART_SPACING` | `[(ref, ref, min mm)]` | J | |
| `POLARITY_MARKS` | `[ref]` | K | |
| `FIDUCIALS` | `{"min": n, "clearance_mm": mm}` | M | |
| `BOTTOM_PARTS` | `[ref]` | N | |
| `EXCLUSIONS` | `[(id, text, (ref patterns), (nets))]` | O | |
| `PORT_EDGES` | `{ref: ("top" or "bottom" or "left" or "right", max mm)}` | P | |
| `CONTACT_RATINGS` | `{ref: {"amps": A per contact, "derate": fraction}}` | E | where the current comes from is decided in WP18 |
| `HOT_LOOPS` | `[{"name", "refs", "pads", "max_mm2"}]` | H, after wp15 | |
| `HEIGHT_MAX_MM` | mm | Q, after WP14 part files | |

### D5. Records (item 3)

The existing keys stay as they are: `hottest resistor mW` (top four) and `cap stress (V, rated)` (top four), because reports and reviews may read them. New keys hold every part:

- `resistor power`: `{ref: {"mW", "limit_mW", "scenario"}}`, where `scenario` is the index into `STRESS_SCENARIOS` of the worst case.
- `capacitor voltage`: `{ref: {"v", "rated" (null when missing), "target", "mpn", "scenario"}}`.
- `supply pins`: `{"U1 pad 1": {"v", "min", "max"}}` (`test_supply_pins_in_range` gains the `record` fixture).
- `supply limits`: `{what: {"value", "op", "limit", "ok"}}`.

Every check records before it asserts. The failure precedence (reverse bias, then a missing rating, then over target) and the message text stay exactly as now. `report.py` shows a value with more than eight entries as `(N entries: see results.json)`, so the report stays readable.

### D6. Per-part derating (item 4)

`CAP_DERATE = {mpn: fraction}` is optional and defaults to 0.8. The options were tuples inside `CAP_VRATED`, which breaks every board's table, or derating by class pattern, which is too clever. Recommended: a separate optional table.

- A fraction above 0.8 is refused, so the table can only tighten (**owner's call**: some film capacitors are rated for continuous full voltage).
- An entry whose MPN has no `CAP_VRATED` row fails, naming it.
- The `review-derating` 62.5 % workaround text goes.

### D7. `pcbkit parts stock` (item 5)

- Runs anywhere (tier 1).
- Reads `design.py` and the project's `bom.py` through `bom.bom_lines(parts, through_hole=set(), overrides)`. Through-hole status does not matter to stock.
- Asks Mouser with `mouser.part_number(batch, exact=True)` (directly through `mouser.send` and `cache.write`, not `parts.ask_mouser`: see "Slice 5 review changes"), ten part numbers per request, spaced like `datasheet_find`. It picks the match whose squashed part number equals the line's (`datasheets.squash`).
- Options:
  - `--qty N` boards (default 1);
  - `--max-age HOURS` (default 24, from `STOCK_MAX_AGE`);
  - `--offline` (use the cache at any age; never ask Mouser).
- Prints JSON, one object per BOM line: `item`, `refs`, `mpn`, `mfr`, `qty_per_board`, `needed`, `needed_total`, `order_qty`, `status`, `fetched` (ISO date, or null), `age_hours`, `mouser` (`part_number`, `manufacturer`, `stock`, `availability` as raw text, `lifecycle`, `lead_time`, `price_breaks` as `[{"qty", "price" raw, "price_value", "currency"}]`, `unit_price_at_order`, `min`, `mult`, `suggested_replacement`).
- `status` is one of `ok`, `short`, `not-active`, `not-found`, `ambiguous`, `no-mpn`, `not-asked` (no key and nothing cached), `unreadable` and `stale` (see "Slice 5 review changes").
- Exit codes: 2 if any line is `not-asked` or `stale`, else 1 if any line is not `ok`, else 0 (**owner's call**; `stale` added by the slice 5 review).
- Logic goes in a new `pcbkit/stock.py`: pure functions from a Mouser answer to rows, plus the batching. `pcbkit/mouser.py` is not touched; its plainness test stays.
- The Mouser field names are assumed until M3 confirms them.

### D8. Necks, slivers and islands (item 7): not in WP17

Islands are removed by `post`. Slivers may already fail `test_drc_clean_with_schematic_parity`, since that runs DRC with every severity on. Necks are caught by nothing unless KiCad's `connection_width` constraint is in a `.kicad_dru`. All three need M4 on the Mac. If M4 shows a rule catches necks, WP18 adds it to the rules pcbkit writes (`pcbkit/route/rules.py`) and `review-board` drops those items. That is a routing change, not a check, which is a second reason to keep it out of here.

## The checks in WP17

Common to all of them:

- Gate `sch`, fixtures `nl` (and `circuits` where a solve is needed).
- Tier: the run needs only kicad-cli, so it is tier 1 if D1's owner's call is accepted.
- Each engine is a pure module under `pcbkit/check/`, unit-tested on a hand-built `Netlist` (`Netlist.from_text`, as `tests/unit/test_check_netlist.py` does).
- Each check module is proved end to end on its own toy board in a new `tests/integration/test_check_<group>_<topic>.py`, built with `tests/check_toy.Toy` and served by `fake_nl`.
- A planted mistake turns exactly the named check red. The control first passes.

### A. Fault states: `test_fault_state_pins[condition]` (`power`)

- **Reads:** `FAULT_STATES`, `circuits.scenario`.
- **Rule:** in each condition, every listed pin is driven (not floating) and its voltage is inside the window. The window comes from the part's input thresholds in its datasheet (VIL/VIH of the enable). The intended safe level is the one `walk-fault-states` settled with the user, and the row's comment gives the source.
- **How:** `circuits.scenario(nl, **row["scenario"]).solve()`, then `Solution.floating(net)` and `solved[net]`. A pad with no net fails, naming it. A solve that does not converge fails with "the DC solve did not converge in condition X", not a traceback.
- **Records:** `{"U2 pad 5": [V, min, max, "driven" or "floating"]}` per condition.
- **Planted mistakes:**
  - (1) Remove the pull-down. The solver's 1 nS leak says 0.0 V, which is inside a (0, 0.4) window, so a check that trusted the voltage would pass. This check must fail with "floating". This is the plant that matters most.
  - (2) Move the pull to the other rail: outside the window.
  - (3) A row names a pad that is not in the netlist.
- **Honest about the model.** `dc` solves resistors, LEDs, Schottkys and three known FETs as ideal switches with one Vth. It has no ICs: no regulator turns its output off when its enable drops, no internal pulls, no ESD diodes, no Zener breakdown, no BJTs. A floating net reads 0 V because of the leak. So A checks exactly what the project's `scenario` says each condition is. "Controller absent, so +5V is off" must be written into the scenario by hand (`fix("+5V", 0)`). A rail the scenario leaves unfixed is floating, and the pins it pulls fail as floating, which is the safe direction. The docs say so in plain words. Chasing consequences (enable low, so output 0, so the next enable low) is a modelling layer of its own: a later WP.
- **Size:** medium. Cheap code; the risk is in the floating semantics and the messages.

### B. Power paths (`power`)

- **Checks:**
  - `test_power_path[from->to]`: per row, a path exists, and only through the named parts;
  - `test_no_unlisted_power_path`: no other pair of `nets` is joined;
  - `test_power_path_parts_are_modelled`: no part bridges two judged nets unless it is declared.
- **Reads:** `POWER_PATHS`.
- **Rule:** DC conduction. Spec intent ("source X powers Y only", back-feed) is review-spec-drift claim 64 and walk-fault-states candidate 5.
- **How:** a conduction graph of nets.
  - Resistors are undirected, weighted by R.
  - A two-pin part with pins named A and K is a diode, directed A to K, with a small weight.
  - A three-pin part with G, D and S is a FET whose channel is undirected, which also covers its body diode.
  - `POWER_PATHS["parts"]` adds directed edges for ICs: regulator in to out, a load switch, an LDO's reverse path if the user declares it.
  - Pin functions come from the netlist nodes, so any symbol library works if it names its pins that way (M9). `dc.build` is not changed, so no existing solve moves.
  - From each judged net, a least-resistance walk (Dijkstra) that never passes through another judged net. Paths compose through the rows, so VBUS to +5V to +3V3 needs no VBUS to +3V3 row.
  - A path counts if its resistance is at most `max_ohm`. A row passes if a path exists and removing the named parts' edges leaves none.
  - Any part not modelled and not in `parts` with pins on two or more judged nets fails as unmodelled. `{"U1": []}` declares "passes no power".
- **Records:** per pair, the cheapest path's refs and ohms; the unmodelled parts.
- **Planted mistakes:**
  - (1) A P-FET with drain and source swapped, so its channel and body diode join +3V3 to VBUS: the unlisted-path check fails.
  - (2) The regulator's output pad moved to another net: "declared path VIN->+5V through U3 not found".
  - (3) A new IC across +5V and +3V3 that is not declared: the unmodelled check fails.
- **Honest about the model.** This is static: a FET counts as able to conduct whatever its gate does, which is right for "can this ever back-feed". Back-feed through an unpowered IC's ESD diodes runs through kilohms of series resistance, so it falls under `max_ohm` and is not judged here. `GPIO_SCENARIOS` covers the ESP32 case, and a general model is later. `max_ohm` is a judgement (default 10 Ω, **owner's call**). The record shows the cheapest path's resistance, so a review sees near misses.
- **Size:** large. The modelling, the threshold and the unmodelled-part rule are where it can be wrong.

### C. Port map (`ports`)

- **Checks:** `test_port_pins_carry_their_nets[ref]` and `test_every_connector_is_in_the_port_map`.
- **Reads:** `PIN_NETS`.
- **Rule:** the spec's port map. Spec-drift candidates 56 and 57.
- **How:** for each listed ref, every pad's netlist net equals the row (`None` means unconnected), and every connected pad of the ref is listed. A connector is a part whose symbol library starts with `Connector`, or whose reference starts with `J` (**owner's call**). Each must be a key. Controller pins may be listed too, without the completeness rule.
- **Records:** the port map as found, for every connector, even when the check fails.
- **Planted mistakes:**
  - (1) Two nets swapped on a connector.
  - (2) A new connector that is not listed.
- **Size:** small.

### D. Mis-plug: `test_misplug_lands_nothing_harmful[plug]` (`ports`, after C)

- **Reads:** `MATING`, `PIN_NETS` (for each port's pad set, numbered 1..m), optional `NET_LIMITS`, `SUPPLY_SCENARIO`, `circuits.scenario`.
- **Rule:** wire i of a plug is meant to meet the net `pins[i]`.
- **Cases per port in `fits`:**
  - Straight, at offset 0, always.
  - Unkeyed plugs also get reversed and offset cases. Reversal is a 180° rotation, pad k to N+1−k, which holds for single rows and for KiCad's zig-zag 2xN numbering (M8). Offsets go in steps of `rows`, one column overhanging either end. A wire that lands off the end lands nowhere.
  - Exactly one case across all ports must be the intended one; if none is, the check fails.
- **Voltages** come from one solve of `SUPPLY_SCENARIO`. A net is driven if the scenario fixes it.
- **Fails on:**
  - (a) a source fight: a wire on driven net A lands on a different driven net B, with |V(A) − V(B)| > 0.05 V. This includes supply onto ground.
  - (b) over-voltage: a wire on driven net A lands on undriven net B with `NET_LIMITS[B]` < V(A).
- **Records:** for each plug, each port, each case: the landing table (position, wire net, pad, board net, verdict). It also records, but does not fail on, adjacent pads at different voltages (single row: k±1; two rows: k±1 within a pair and k±2 along a row). Whether a supply beside ground on a header is acceptable is still `review-misplug`'s judgement (**owner's call**: make it fail unless the pair is listed in an `adjacent_ok` key on the plug).
- **Planted mistakes:**
  - (1) An unkeyed 3-wire plug whose reversal puts +5V on GND: a fight.
  - (2) An offset that puts +5V on a pad listed at 3.6 V in `NET_LIMITS`.
  - (3) `keyed` true: the same toy passes.
  - (4) A plug that fits no port as intended.
- **Honest:** harm is judged only for source fights and declared limits. What a signal wire suffers is recorded, not judged.
- **Size:** large. The semantics are new, and the 2xN numbering is assumed until M8.

### F. Semiconductor voltage: `test_semiconductor_voltage_derated` (`power`, after slice 4)

- **Reads:** `SEMI_VRATED`, `STRESS_SCENARIOS`, `circuits.scenario`.
- **Rule:** |Vds|, |Vgs| and diode reverse voltage stay under `derate` (default 0.8) of the rating at the worst stress point. This is the capacitor rule's twin (`review-derating` part classes).
- **How:** parts with pins named G/D/S or A/K, or with `pins` given in the row, are judged. A judged part with no rating on file fails, as capacitors do. A part whose kind pcbkit cannot read pins for, and with no `pins` in its row, fails, naming it. It reuses the stress solve that slice 4 moves to `pcbkit/check/stress.py`.
- **Records:** every part: worst value, rating, target, scenario index.
- **Planted mistakes:** a 5 V Vds rating on a FET that sees 12 V; a missing rating.
- **Size:** small to medium.

### R. Exact bus scan: `test_i2c_bus_answers_exactly_the_expected_addresses[bus]` (`bringup`)

- **Reads:** `I2C_EXPECTED`, `I2C_BUSES`, `circuits.i2c_devices`.
- **Rule:** the addresses found (on-board plus off-board, with any all-call or general-call addresses the project's `i2c_devices` reports) equal the expected set. Bring-up candidate 42.
- **Records:** expected, found, missing, extra.
- **Planted mistakes:** an address strap moved to the other rail; a bus named in `I2C_EXPECTED` that is not in `I2C_BUSES`.
- **Size:** small.

### S. Enable links: `test_each_regulator_on_a_shared_enable_has_its_own_link[net]` (`power`)

- **Reads:** `ENABLE_LINKS`.
- **Rule:** each regulator on a shared enable can be enabled alone at bring-up (candidate 40).
- **How:** for each (ref, pad), the pad's net differs from the shared net, and one two-pin link part joins the two nets. A link is a `Device:R` whose value parses to 0, or a part from a `Jumper` library. No two regulators may share a link.
- **Records:** `{net: {ref: link ref}}`.
- **Planted mistakes:**
  - (1) Both enables wired straight to the shared net.
  - (2) The 0 Ω link replaced by 10 kΩ.
  - (3) Two regulators behind one link.
- **Size:** small. The brief's "a rail that cannot be isolated" for outputs is the same engine on a `RAIL_LINKS` table: a later row if the owner wants it.

## Slices

All code keeps the 3.9 floor. The gate after every slice is:

- `uv run pytest tests/unit -q`
- `uv run --python 3.9 pytest tests/unit -q`
- `uv run pytest tests/integration/test_check_plugin.py tests/integration/test_check_*_toy*.py -q` (no KiCad, see the codebase differences, item 3)
- `uv run ruff check . && uv run ruff format --check .`

Every new check gets the planted-mistake proof: plant it, watch the named test go red, delete `__pycache__`, watch it go green. The reviewer gets the red output.

How shared files are handled:

- `docs/project-interface.md` is edited by most slices. Each slice owns one block (its group's table, or its rows), and these slices merge one at a time, rebased.
- `tests/check_toy.py` is read-only for every slice. Each slice builds its own toy in its own test module.
- New-check integration tests select their own module (`-k <module>`) and assert per-check outcomes, so a sibling module landing in the same group cannot turn them red. Slice 13 adds the one control run with every new table.

Order:

1. Slice 0.
2. Slice 1.
3. Slices 2 and 3 (may run in parallel, merge one at a time).
4. In parallel: {4, 5, 6 (A), 7 (C), 8 (S), 9 (B), 10 (R)}.
5. 11 (D) after 7; 12 (F) after 4.
6. 13 last.

| In parallel after 3 | Its own files | Shares only the docs block rule with |
|---|---|---|
| 4 records and derating | `check/builtin/test_circuit.py`, `check/stress.py`, `report.py` | all |
| 5 parts stock | `pcbkit/stock.py`, `cli.py` (the `parts` group only) | all; `cli.py` serial after 2 |
| 6 A | `check/faults.py`, `builtin/test_faults.py` | all |
| 7 C | `check/ports.py`, `builtin/test_ports.py` | all |
| 8 S | `check/links.py`, `builtin/test_enable_links.py` | all |
| 9 B | `check/power_paths.py`, `builtin/test_power_paths.py` | all |
| 10 R | `builtin/test_i2c_expected.py` | all |

### Slice 0: sign-off sheet (owner, no code)

- **Goal:** the owner settles every "owner's call" above: the gate names, the tier-1 schematic gate, the group names, every row of the D4 shape sheet, the `CAP_DERATE` ceiling, `max_ohm`, the connector rule, the `pcbkit specs` name and the `parts stock` exit codes.
- **Touches:** this plan only (the answers appended).
- **Acceptance:** each call has an answer. Slices that need a table do not start until their table is signed off.
- **No KiCad needed:** yes.

### Slice 1: the gated run

- **Goal:** D1 in full.
- **May touch:**
  - `pcbkit/check/results.py`: `GATES`, `BUILTIN_GATES` and a pure `gate_of(module, function, fixturenames, marked)`.
  - `pcbkit/check/plugin.py`: the option, the marker, `pytest_collection_modifyitems`, and the new results fields in `pytest_sessionfinish`.
  - `pcbkit/check/runner.py`: `command(..., gate=None)`, `run(..., gate=None)`.
  - `pcbkit/cli.py`: `check --gate`, and the `require_pcbnew` skip if signed off.
  - `pcbkit/report.py`: the gate in `partial_note` and the summary.
  - `skills/order-pcbway/scripts/preflight.py`: the message names the gate.
  - `docs/project-interface.md` ("Results" and the JSON example), `docs/writing-checks.md` (the marker), `.claude/CLAUDE.md` (the tier line).
  - Tests: `tests/unit/test_check_runner.py`, `test_check_report.py`, `test_check_cli.py`, `test_order_preflight.py`, a new `tests/unit/test_check_gates.py`, and `tests/integration/test_check_plugin.py`.
- **Must not touch:** anything under `pcbkit/check/builtin/`, `copper.py`, `coupling.py`, `stitching.py`, `reserved.py`, `project.py`.
- **Acceptance (judged from evidence):**
  - `test_check_gates.py` holds a frozen table of every built-in check function and its gate, matching D1, found by reading the modules' syntax trees. A new or renamed check without an entry fails it.
  - A toy run with `--pcbkit-gate sch` writes `"gate": "sch"`, has entries only for schematic-gate checks, has `waiting` naming the board-reading project check with `"route"` and the `@pcbkit_gate("order")` one with `"order"`, has `selection.gated_out` equal to `len(waiting)`, and has `complete` false.
  - The same toy with no gate writes `"gate": null`, an empty `waiting` and `complete` true. With `order` it also writes `complete` true.
  - `pcbkit report` and the preflight name the gate.
  - With no pcbnew on the fake machine, `pcbkit check --gate sch` reaches the runner and `pcbkit check` stops at the pcbnew guard (if signed off).
- **Planted mistakes:**
  - (1) Drop the DRC exception: the frozen table goes red.
  - (2) Read the test function's own arguments instead of `item.fixturenames`: `test_outline_size` (through `gerbers`, then `fab_dir`) gets `sch` and goes red.
  - (3) Ignore the marker: the toy's order-marked check runs at `sch` and goes red.
  - (4) Count gated checks as `-k` deselections: the `gated_out` assertion goes red.
- **No KiCad needed:** yes. M1 and M2 confirm it on a real board.
- **Size:** medium. Several readers of `results.json`, and the plugin hook order.

### Slice 2: the table validator and `pcbkit specs` (after 1)

- **Goal:** D3, and D4's shapes for the existing tables and for the signed-off proposed tables.
- **May touch:**
  - `pcbkit/check/tables.py` (new).
  - `pcbkit/check/plugin.py` (`spec_params` catches `TableError`).
  - `pcbkit/cli.py` (the `specs` command).
  - `docs/cli.md` (its directive).
  - `docs/project-interface.md`: a "Table shapes" paragraph and a "Proposed tables (validated, no built-in check yet)" block for the WP18 names.
  - Tests: `tests/unit/test_check_tables.py` (new), `tests/unit/test_check_docs.py` (the describe-in-docs test), `test_cli.py`.
- **Must not touch:** any built-in check module or engine.
- **Acceptance:**
  - Each shape primitive has a passing example and a refused one with the exact message, path included.
  - The registry finds `TABLES` in a scratch module without a shared list.
  - `pcbkit specs` on a scratch project with a bad `FAULT_STATES` row prints `specs.FAULT_STATES["c"]["pins"][0]: ...` and exits 1; on a good one it exits 0.
  - The toy's existing `SPECS` validate clean, which is evidence that the validator is as permissive as the checks.
  - A `TableError` raised while parametrising fails that one check by name in a toy run.
- **Planted mistakes:** a `Window` that accepts min > max; a `Pad` that refuses ints (the toy's `SUPPLY_PINS` uses `1`) goes red.
- **No KiCad needed:** yes. M7 checks it on real boards.
- **Size:** medium.

### Slice 3: groups and netlist helpers (after 1; parallel with 2, merged one at a time)

- **May touch:**
  - `pcbkit/project.py`: `CHECK_GROUPS` gains `power`, `ports`, `bringup`.
  - `pcbkit/check/results.py`: `BUILTIN_GROUPS` maps the WP17 module stems listed in D2.
  - `pcbkit/check/netlist.py`: `Netlist.functions(ref) -> {pad: function}`.
  - `docs/project-interface.md`: the `checks.groups` row only.
  - Tests: `tests/unit/test_project.py`, `test_check_netlist.py`, and the collected-groups test in `tests/integration/test_check_plugin.py`.
- **Acceptance:**
  - A toy with `power` switched on and no module yet collects nothing and reports nothing.
  - `functions` returns the KiCad 10 suffix-stripped names.
  - The groups test lists the new groups.
- **Planted mistake:** map a module to the wrong group: the collection test goes red.
- **No KiCad needed:** yes.
- **Size:** small. It is the one WP17 slice that touches wp15's `project.py`.

### Slice 4: circuit records and `CAP_DERATE` (after 2, 3)

- **May touch:**
  - `pcbkit/check/builtin/test_circuit.py`.
  - `pcbkit/check/stress.py` (new: `stress_solutions(nl, specs, circuits)` yielding (index, solution)).
  - `pcbkit/report.py` (long values).
  - `docs/project-interface.md` (the circuit rows).
  - `skills/review-derating/references/part-classes.md` (line 20).
  - Tests: `tests/integration/test_check_plugin.py` (new plants), `tests/unit/test_check_report.py`.
- **Must not touch:** `pcbkit/check/dc.py`.
- **Acceptance:**
  - Every resistor, capacitor, supply pin and limit row appears in its check's `numbers`, with its scenario index.
  - The top-four keys are unchanged, byte for byte, in the toy.
  - A failing capacitor run (missing rating, reverse bias) still records every other capacitor, and the failure message is unchanged: the existing plants' `says` strings still match.
  - `CAP_DERATE {"TANT-10U": 0.5}` fails a tantalum at 60 % that passes without the row.
  - 0.9 is refused by the validator. An MPN that is not in `CAP_VRATED` fails, naming it.
- **Planted mistakes:** the tantalum at 60 %; record after the assert (the missing-rating run then has no `capacitor voltage` key) goes red.
- **No KiCad needed:** yes.
- **Size:** small to medium.

### Slice 5: `pcbkit parts stock` (after 2: `cli.py`)

- **May touch:**
  - `pcbkit/stock.py` (new).
  - `pcbkit/cli.py` (the `parts` group and `stock`).
  - `docs/cli.md`, `docs/ordering.md` (one paragraph).
  - `tests/unit/test_stock.py` (new), `tests/unit/test_cli.py`.
  - `tests/unit/test_plugin.py` (`command_problems` follows a group into its subcommand, with its own planted test).
- **Must not touch:** `pcbkit/mouser.py`, `pcbkit/cache.py`, `pcbkit/parts.py`.
- **Acceptance:**
  - With `_transport` faked and a recorded answer of the assumed shape (generic part numbers), the JSON has every field in D7.
  - `needed` = qty per board × `--qty`. `short` is set when stock < needed.
  - `--offline` with an empty cache gives `not-asked` and exit 2.
  - A cached answer older than `--max-age` is refetched, and `fetched` shows the new date.
  - No request URL and no header text appears in any output.
- **Planted mistakes:** price breaks read at the per-board quantity instead of the total (`unit_price_at_order` test red); stale cache accepted (age test red).
- **No KiCad needed:** yes. M3 confirms the field names.
- **Size:** medium. The field names are unknown.

### Slices 6 to 12: the checks

Each slice's files are its engine, its check module, `TABLES` in the engine, its docs block, and its tests:

- engine tests in `tests/unit/test_check_<engine>.py`;
- toy tests in `tests/integration/test_check_<group>_<topic>_toy.py`.

None may touch `dc.py`, `netlist.py`, `plugin.py`, `results.py`, `tables.py` or another slice's files; a slice that needs one changed stops and asks. Acceptance for each:

- the engine tests pass;
- the toy control passes;
- each planted mistake listed in its section above turns exactly that check red, with the stated message;
- `numbers` holds the stated records;
- the table's shape is in the docs block (the slice 2 test).

All are verifiable with no KiCad. Sizes: A medium, B large, C small, D large, F small to medium, R small, S small.

### Slice 13: the reviews hand over (last)

- **May touch:**
  - `skills/*`: the gate in each review's "run the checks" step, `walk-fault-states` with pads, the built tables no longer marked proposed, `review-sourcing` reading `pcbkit parts stock`, `review-derating` reading the new records.
  - `docs/claude-plugin.md`, `plans/wp16-review-skills.md` (the triage verdicts for A, B, C, D, F, R and S become Built), `docs/changelog.md`.
  - `tests/integration/test_check_plugin.py`: one toy with every WP17 table, every new group on, all passing.
  - The plugin eval cases if WP16 step 6 has landed.
- **Acceptance:**
  - `test_plugin.py` passes, so every `pcbkit check --gate` and `pcbkit parts stock --qty` mention is a real option.
  - No skill tells Claude to judge something a WP17 check decides; a reviewer reads each skill against the triage table.
- **No KiCad needed:** yes.
- **Size:** medium. Many files, little code.

## Measured on the owner's Mac (KiCad 10.0.6)

| K | Question | Experiment | Assumed meanwhile |
|---|---|---|---|
| M1 | Which checks are clean at each gate | Generic blinky template: `pcbkit sch`, `pcbkit check --gate sch`; `pcbkit build`, `--gate place`; `route`, `--gate route`; `finalize`, `--gate order`. Record each run's counts and failures. | Clean at each gate, with D1's table. |
| M2 | Is the schematic gate tier 1 in practice | In a venv with no pcbnew: `pcbkit check --gate sch` on blinky | Runs. The netlist and ERC need only kicad-cli. |
| M3 | Mouser's field names, price format and maker strings | Print `SearchResults.Parts[0]` keys and one `PriceBreaks` entry from a cached answer in `~/.cache/pcbkit/mouser` (a generic part from `tools/datasheet_sources/parts.csv`) | `Availability`, `AvailabilityInStock`, `LifecycleStatus`, `LeadTime`, `Min`, `Mult`, `SuggestedReplacement`, `PriceBreaks[{Quantity, Price, Currency}]`, price as text with a currency sign and a dot decimal; also record Mouser's real maker strings (`Manufacturer`) for the makers on a real BOM, to check `stock.same_maker` |
| M4 | Necks, slivers, islands | On a generic finalized two-layer board: (a) draw a track meeting a pour edge at about 10°, then `kicad-cli pcb drc --severity-all`: is `copper_sliver` reported? (b) add a `.kicad_dru` with `(rule neck (constraint connection_width (min 0.5mm)))`, cut a 0.3 mm pour neck: reported? Does the `.kicad_dru` survive `SaveBoard` and `finalize`? (c) any `isolated_copper` after `post`? | (a) yes, (b) yes but the file is not kept, (c) none |
| M7 | The validator against real boards | `pcbkit specs` on the owner's own boards. Report only the error count and the table names, never the contents. | 0 errors |
| M8 | 2xN pad numbering | Read the pad positions of `PinHeader_2x05_P2.54mm_Vertical` and a 1x04 JST footprint | Odd pads in one row, even in the other; a 180° turn maps k to N+1−k |
| M9 | Pin function names in stock symbols | Export a netlist of a schematic with `Device:D`, `D_Schottky`, `D_Zener`, `LED`, `Q_NMOS_GSD`, `Q_PMOS_GSD`, `AO3401A`, `2N7002` | Diodes A/K, FETs G/D/S |

## Collisions with the branches in flight

| File | WP17 slice | wp15-four-layer | wp14-parts-extract |
|---|---|---|---|
| `pcbkit/check/plugin.py` | 1, 2 | slice 6, line 469 (`copper_mm` in results) | |
| `pcbkit/project.py` | 3 (`CHECK_GROUPS`, line 43) | slice 1 (adds constants just below it, at lines 47-55) | |
| `docs/project-interface.md` | most | slices 1 and 7 (`[stackup]` rows beside the `checks.groups` row at line 103) | |
| `tests/unit/test_project.py`, `tests/unit/test_plugin.py` | 3, 5 | slice 1 (`test_plugin.py:1259-1261`; WP17 edits about line 250) | |
| `.claude/CLAUDE.md` | 1 (tier line), Layout lines for new modules | | adds two lines |
| `check/builtin/test_copper.py`, `test_fab.py`, `test_outputs.py`, `copper.py`, `coupling.py`, `stitching.py`, `reserved.py` | none (D1's central table is there to avoid them) | yes | |
| `pcbkit/citations.py` | none | | new |

Merge order:

1. `wp16-review-skills` to main first: WP17 is cut from it.
2. `wp14-parts-extract` at any time. Its only overlap is a CLAUDE.md line.
3. WP17 before wp15's slice 6. wp15 waits on Mac spikes and is the larger branch, and its overlap with WP17 is one line in `plugin.py` and a few constant lines in `project.py`, which are easier to rebase on its side.
4. If wp15's slice 1 lands first, WP17's slice 3 rebases onto it. Its one-line `CHECK_GROUPS` change is near wp15's new constants and may need a hand merge.
5. WP18's two-layer I checks `stackup.layers == 2` and says "four-layer reference planes come later" on four layers. It can merge before or after wp15. The four-layer I and H come after both.

## Risks

- **A gate assigned wrongly.** Too early fails loudly. Too late is caught by the full run before ordering. The frozen gate table makes every change visible.
- **A project check that reads files through `project`** derives `sch` and runs too early. The docs and `add-check` tell project checks to use the marker. `check-writer` gets one line.
- **Floating semantics in A** could refuse a pin driven only by a current source (`Circuit.inject`). Documented. If it bites, `floating` counts current sources as drives, in a later slice that touches `dc.py`.
- **B's threshold and its unmodelled-part rule** may be noisy on real boards (many ICs touch two rails). The `{"U1": []}` declaration is the escape. M9 settles the pin names.
- **D's harm rules are narrower than the review's judgement.** The landing table is recorded, so the review starts from it and does not walk the plug again.
- **Shapes are interface.** A shape changed after slice 13 breaks reviews and boards. Slice 0 settles them once.
- **Existing tables are not enforced** inside existing checks until M7. Until then `pcbkit specs` is the only enforcement.
- **The docs file every slice touches** is the merge bottleneck; the one-block-per-slice rule keeps conflicts mechanical.
- **Mouser:** rate limits unconfirmed (WP14), field names assumed (M3). The key never reaches output.

## Size

Sized by complexity, unknowns and risk.

**WP17 is large:** 14 slices.

- B and D are large: new modelling and harm semantics.
- 1, 2, 5 and A are medium.
- The rest are small.

The unknowns are M1, M3, M8 and M9, none of which blocks a slice here. If the owner wants it smaller, the cut is after slice 10: B and D (with C) become a WP of their own, because they carry most of the modelling risk.

**WP18 is large, and its unknowns are measured ones:** every board read is Mac-verified.

## WP18 sketch: board checks

Every slice splits a pure engine (positions and polygons in, verdict out), unit-tested here, from a thin pcbnew adapter, proved by a `kicad`-marked test on a board built in `tests/check_group_boards.py`. Gates come from the marker. Slices:

1. **G, test points** (`bringup`, `place`).
   - Reads `TEST_POINTS` and `PROBE_KEEPAWAY`.
   - Rule: per net, a test point (reference `TP` or a `TestPoint` footprint) has a pad on the net and its centre outside every keep-away box. A ground probe point (a GND test point, or a plated through-hole GND pad) lies within the distance, also outside the boxes.
   - Records each net's test point, its position, the nearest ground point, the distance and the side.
   - Plants: ground point too far, test point inside a box, test point deleted.
   - Measure first: test point references and pad attributes on the Mac.
2. **I, two-layer reference** (`planes`, `route`).
   - Reads `REFERENCE_PLANES` and `GROUND_NET`.
   - Samples the net's tracks every 0.1 mm. Every point beyond the exemption radius around the net's own vias and pads must lie over GND fill on the other layer (`GetFilledPolysList(...).Contains`).
   - The length that does not counts against the row's maximum, default 0.
   - Records the unreferenced length and the worst spots in layout mm, so `review-board` looks at those spots.
   - Plant: a bottom track cutting the pour under a top fast track.
   - Measure first (M5): the exemption radius, as via pad radius plus zone clearance plus half the track width, on a real via.
3. The neck/sliver rule, if M4 says so (`route/rules.py`, the `.kicad_dru`).
4. Existing checks read their tables through `tables.read` (after M7).
5. J, M, N, P (cheap placement geometry), then K (pin-1 marks: a geometric judgement, medium), E (contact current: decide where the current comes from first), L (`CONN_LABELS`, netlist plus silk.py, small), O (exclusions, small).
6. Later: H with wp15, Q with WP14 part files. T goes into `pcbkit quote`, not a check. U needs a design first.

## Critical files

- `pcbkit/check/plugin.py`
- `pcbkit/check/results.py`
- `pcbkit/check/builtin/test_circuit.py`
- `pcbkit/check/dc.py`
- `tests/integration/test_check_plugin.py`

## Slice 0 answers (2026-11-12)

The owner deferred every call to the plan's recommendation. Recorded here so the slices can start.

| Call | Answer |
|---|---|
| Gate names | `sch`, `place`, `route`, `order` |
| Schematic gate without pcbnew | Yes: `pcbkit check --gate sch` runs wherever kicad-cli is installed |
| Group names | `power`, `ports`, `bringup` (WP17); `planes`, `placement` (WP18) |
| D4 shape sheet | As written, every row |
| Pins in tables | Pad numbers, never pin names; the `walk-fault-states` example changes to match |
| `CAP_DERATE` ceiling | 0.8: the table can only tighten. Revisit if a board needs a film capacitor at full voltage |
| `max_ohm` default | 10 Ω |
| What counts as a connector | Symbol library starts with `Connector`, or reference starts with `J` |
| Validator command | `pcbkit specs` |
| `parts stock` exit codes | 0 all `ok`, 1 otherwise, 2 when Mouser could not be asked and nothing was cached |
| Adjacent pads in the mis-plug check | Recorded, not failed; `review-misplug` keeps that judgement |
| WP17 scope | Kept whole (B and D stay in) |

## Change to slice 3 (2026-11-12)

Three existing tests hold `BUILTIN_GROUPS` and `CHECK_GROUPS` to the modules that exist (`test_check_gates.py::test_every_built_in_module_is_in_a_group`, `test_check_plugin_helpers.py::test_builtin_groups_name_exactly_the_check_modules_in_the_folder` and `::test_every_group_a_project_may_switch_on_has_a_module_or_a_pack`), so a group or a module stem cannot be declared ahead of its module. Those tests are right and stay as they are. Slice 3 is therefore only `Netlist.functions`. Each check slice adds its own `BUILTIN_GROUPS` row with its module, and the first slice in a group also adds the group to `CHECK_GROUPS` and the `checks.groups` docs row. That makes `pcbkit/check/results.py`, `pcbkit/project.py` and that docs row shared one-line edits: the check slices merge one at a time.

## Slice 2 follow-ups (2026-11-12)

The nineteen follow-ups of the independent review of slice 2 are done (`pcbkit/check/tables.py`, the `specs` command in `pcbkit/cli.py`, the `TableError` handling in `pcbkit/check/plugin.py`, the docs and the tests). The rule held for each existing shape: exactly as permissive as the check that reads the table, and refusing what makes that check crash. Two of the review's counts differ from the code: `EXISTING` holds 55 shapes, not 49, and each now has a positive example in `tests/unit/test_check_tables.py`; the copper and fab group `specs.py` texts moved to `tests/check_group_specs.py`, which the kicad-marked integration tests import back (their text is unchanged byte for byte).

For WP18: holding every existing table's docs row to `describe(shape)` is still to do. Only the proposed tables' rows are generated from the shape and checked against it (and now against the Shape cell, not the whole line); the rows of the existing tables are prose, so a shape can drift from its row. WP18 wires `tables.read` into the existing checks, and that is the moment to put each shape in its row and test it.

## Slice 5 review changes (2026-11-12)

The independent review rejected slice 5 for four false `ok` results. The rule behind every fix: when something cannot be known (a stale answer, an unknown lifecycle text, an uncertain maker, an unreadable field), the line is not `ok` and `notes` says why.

- **Statuses:** `ok`, `short`, `not-active`, `not-found`, `ambiguous`, `no-mpn`, `not-asked`, `unreadable`, `stale`.
  - `stale`: not `--offline`, and the answer used is older than `--max-age` or dated in the future. The `mouser` block, the age and a note are kept. With `--offline` the status is judged normally and the age is on the row.
  - `unreadable` now also covers a lifecycle text outside the allow-list (`null`, empty, `New Product`, `New at Mouser`, `Active`) and not on the known not-active list, a non-text lifecycle, an unreadable `IsDiscontinued`, `Min` or `Mult`, and parts with no `ManufacturerPartNumber`.
  - `ambiguous` also covers the one match being by another maker than the line's (whole name or known alias; never a prefix).
- **Exit code:** 2 if any line is `not-asked` or `stale`, else 1 if any line is not `ok`, else 0. The JSON is on stdout only with those codes; a usage error (2) and a missing project (1) print a message.
- **New row fields:** `needed_total` (the part's total over every BOM line, by squashed part number, times `--qty`) and `order_qty` (`needed_total` raised to `Min`, then to a multiple of `Mult`; null when no part was matched or `Min`/`Mult` were unreadable). Stock and price are judged at them; `mouser.unit_price_at_order` is the price at `order_qty`.
- `stock.fetch_answers` asks through `mouser.send` and `cache.write` rather than `parts.ask_mouser`, so a cache that cannot be written still yields the fresh answer; `parts.py` and `cache.py` are untouched.

### Second review (2026-11-12)

- **Totals (N1, N4):** every line is matched first; `needed_total` is then summed per matched Mouser part (its Mouser part number, or its number and maker when that is missing), so one part bought by its maker number on one line and by Mouser's own on another is judged on both, and one number from two makers is two parts. A line that matched no part (`not-found`, `ambiguous`, `not-asked`, `unreadable`) gets the sum over lines with the same squashed number; a `no-mpn` line has `needed_total` null.
- **Maker match (N2):** two names also match when one's whole words are the leading whole words of the other ("Microchip" and "Microchip Technology"), after the company words and punctuation are folded away; never a character prefix. This makes "Murata" and "Murata Power Solutions" the same maker; two such candidates for one line stay `ambiguous`. The long names in the tests are from memory: M3 records Mouser's real maker strings.
- **Tests (N3, N7):** a stale `short` or `not-found` line is `stale` with exit 2; the precedence (`no-mpn`, `not-asked`, then `stale` over any judged status, and within a judged line `not-active`, `unreadable`, `short`, `ok`) is pinned.
- **Field name (N6):** `mouser.unit_price_at_order`, the price at `order_qty` (it was `unit_price_at_needed`).
- **Direct asking is a work-around (N5):** `stock.fetch_answers` calls `mouser.send` and `cache.write` only because this slice may not touch `parts.py`, `cache.py` or `mouser.py`. Follow-up: give `cache.fetch` / `parts.ask_mouser` the future-date rule (a future-dated entry is not fresh: today `ask_mouser` treats it as fresh, a latent bug for `datasheet find`) and a keep-on-write-failure path; have `mouser.send` wrap `http.client.HTTPException` as `MouserError` with a retry; then point `stock` back at `parts.ask_mouser`.

## Review follow-ups of slices 4, 7, 8 and 10 (2026-11-12)

The independent review of slices 4, 7, 8 and 10 rejected slice 8 and accepted the other three with follow-ups. All of it is done.

- **Connector rule (correction to the slice 0 answer "Symbol library starts with `Connector`, or reference starts with `J`"):** a connector is a part whose library starts with `Connector` or whose reference letters are exactly `J`, unless it is a test point (letters `TP`, or `TestPoint` in the symbol name or library) or a jumper (letters `JP`, or a library starting `Jumper`). The old rule failed a correct board that has `JP1` or `TP1` with "connectors not in specs.PIN_NETS". The rule does not find a connector from a custom library whose reference is `P1`, `CN1` or `X1`: such a part is listed by hand. The slice 0 row above is left as it was; this is the answer now.
- **Port map messages:** a net written `/VIN` fails with "net names carry no leading slash"; a listed pad the part does not have fails as "not a pad of J1" whether it is given a net or `None`; a ref not in the design records `pins` first and then fails with the short message.
- **Enable links (slice 8):** the check now takes each regulator's link out and walks from its enable net through every other part that can carry a current (any two-pin part but a capacitor, every pad of a `Jumper` part, resistor-pack elements, `R_Network` pads to pad 1), not through a ground or supply net, and fails naming the path if it reaches the shared net or another listed regulator's enable net. A link is any two-pin `Device:R`, `R_Small`, `R_US` or `R_Small_US` whose value is 0 in any of its spellings (`0`, `0R`, `0R0`, `0E`, `R0`, `0 ohm`). A pack element is not a link.
- **DNP link decision:** a link marked DNP is still a link, because the footprint is there to fit at bring-up. The check reads the value only (`DNP` or `DNF`), because the netlist reader does not carry the part's dnp flag; the old unit test that said `DNP` is not a link was changed to say the opposite. A DNP resistor with a real value (`10k DNP`) is not a link and counts as a path in the walk.
- **`netlist.parse_value`:** empty or blank text raises ValueError (it raised IndexError).
- **Records (slice 4):** `unsolved scenarios` (recorded only when a later scenario could not be solved after a reverse-biased capacitor was found), `limit_v` on each capacitor, numpy scalars recorded as plain numbers, a `SUPPLY_LIMITS` row with a bad operator recorded as not ok, and the failure order (reverse bias, missing rating, unknown `CAP_DERATE` part number, over target) pinned by a test.
- **Bus scan (slice 10):** an unreadable `circuits.i2c_devices` result, two devices at one address, an address listed twice in `I2C_EXPECTED` and an SDA net that is not in the design (the `/SDA` spelling especially) fail by name; `devices` (names per address) is recorded.
- **Docs test:** each built table has exactly one row outside the proposed block, in its group's block.
- **Not done:** the changelog line ("`pcbkit report` now shortens any recorded value with more than eight entries, including existing ones") is in `docs/project-interface.md`; `docs/changelog.md` only includes the root `CHANGELOG.md`, which this follow-up may not touch, so the line is still to be added there at release.
