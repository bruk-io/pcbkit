# WP15: four-layer boards

Status: planned on branch `wp15-four-layer`, cut from main at 72d17cd. Slice 1 is being
built on the recommended answers to D1, D2 and D7, which are not yet signed off. A spike
on Bruk's Mac (slice 0) comes before anything that touches routing, because the routing
design depends on what Freerouting 1.9.0 does with plane layers, and nobody has measured
that.

## Goal

Let a board set `[stackup] layers = 4` and get a correct four-layer KiCad board, route,
checks, fab files and quote. The target is signal - ground plane - power plane - signal.
Inner planes are KiCad zones that exist before the autorouter runs, so it never has to
decide them. Each check that reasons about copper should understand the planes and not
quietly ignore them. Every two-layer project must produce the same bytes as on main
wherever a golden or baseline exists.

## Where the codebase differs from the first brief

- **No inner-layer fab limits exist today.** `specs.PCBWAY`
  (`pcbkit/templates/board/specs.py:44-52`) and `check/builtin/test_fab.py` cover outer
  copper only. Slice 5a adds the inner limits; there is nothing to generalise.
- **The layer count is already threaded through in two places.** `place.py:368` calls
  `SetCopperLayerCount(config.stackup.layers)`, and `route/rules.py:42,80` sets it from
  `copper_layers`. Only the schema (`project.py:182-184`) stops a 4.
- **`silk.py` and `docs/writing-checks.md` assume nothing about copper layers.** They
  need a test only, or nothing.
- **pcbkit never edits the DSN or SES text** (`route/freerouting.py:121-128` passes file
  names only). All layer behaviour in the round trip comes from what KiCad exports and
  Freerouting reads, so it can only be measured, not unit-tested without KiCad.
- **Ground is routed by Freerouting today.** `GND` is an ordinary net in the DSN
  (`templates/board/golden/blinky.dsn:165-170`), and the pours land on top afterwards.
  With four layers that would leave plane nets routed as tracks, which is decision D3.
- **`check/copper.py:113-117` uses a fixed 1.6 mm via barrel length**
  (`RHO_CU * 1.6e-3`), whatever `thickness_mm` says. This is an existing inaccuracy. WP15
  leaves the two-layer number alone (it would change outputs) and raises it as open
  question Q5.

## Inventory: what assumes two layers

Kind: **change** (new behaviour), **generalise** (loop over copper layers; two-layer path
unchanged), **test** (nothing to change, prove it on four layers).

| File:line | What it assumes | Kind | Slice |
|---|---|---|---|
| `pcbkit/project.py:66-72` | `StackupConfig` has no inner copper, roles or dielectric | change | 1 |
| `pcbkit/project.py:181-186` | `layers` Rule `choices=(2,)`, note "two-layer boards only" | change | 1 |
| `pcbkit/project.py:362-386` | `parse_config` validates each key alone; no check across keys | change | 1 |
| `pcbkit/place.py:56-80` | `_STACKUP` text: F.Cu, one core, B.Cu; `CORE_LESS_MM` | generalise (2L text untouched; new 4L builder) | 2 |
| `pcbkit/place.py:273-298` | `stackup_text`/`add_stackup` two-layer only | generalise | 2 |
| `pcbkit/place.py:366-422` | `SetCopperLayerCount` already from config; no layer types, no inner copper | change | 2 |
| `pcbkit/kicad/board.py:121-123` | `track` defaults to F_Cu | test (default stays) | 2 |
| `pcbkit/kicad/board.py:163` | `keepout(layers=("F.Cu","B.Cu"))` default | test; pre passes explicit layers (slice 3) | 2/3 |
| `pcbkit/kicad/board.py:265-297` | `clear_spot` collides on F_Cu and B_Cu only | generalise | 2 |
| `pcbkit/kicad/board.py:453-485` | `set_copper_text`: exactly two `[FB].Cu`; inner copper never written | generalise (new inner setter; outer one unchanged) | 2 |
| `pcbkit/route/rules.py:42,80` | `copper_layers=2` default, already passed in | test | 3 |
| `pcbkit/route/hooks.py:22-30,40-57,111-123` | `HOOK_NAMES` and `Hooks`: no plane hook | change | 3 |
| `pcbkit/route/pre.py:97-117` | no planes before the DSN; edge strips use `keepout`'s F/B default (`115-116`) | change + generalise | 3 |
| `pcbkit/route/eco.py:169-186` | `clashes` checks F_Cu, B_Cu | generalise | 4 |
| `pcbkit/route/eco.py:10-13,207-208` | skips only GND copper when copying | generalise (decision D4) | 4 |
| `pcbkit/route/post.py:117-140` | `_drop_orphan_vias`: inside both F and B pours | generalise | 4 |
| `pcbkit/route/post.py:165,200` | `apply_rules(layers)`; `set_copper` outer only | generalise (add inner copper) | 4 |
| `pcbkit/route/stitch.py:36-50` | `ground_pours` returns the F and B GND pours, else None | generalise | 4 |
| `pcbkit/route/stitch.py:104-105,170-171,273-279` | `stitch`/`fill_gaps` test F and B fills; second pass collides on F/B | generalise | 4 |
| `pcbkit/route/cleanup.py:85-98` | `drop_one_sided_vias` looks at zones and pads on F/B only | generalise; **bug on 4L**, see R3 | 4 |
| `pcbkit/route/cleanup.py:127,223` | via width read on F_Cu | test (padstack, K9) | 4 |
| `pcbkit/route/freerouting.py:121-128` | command line; no net-class ignore | test, or change if the D3 fallback is chosen | 4 |
| `pcbkit/check/copper.py:59-127` | `self.layers = (F_Cu, B_Cu)`; one copper thickness; barrel over 1.6 mm | generalise | 5c |
| `pcbkit/check/copper.py:193-211` | joints link front to back only | generalise (chain of adjacent layers) | 5c |
| `pcbkit/check/copper.py:258-283` | heatmap: two panels, "Top copper"/"Bottom copper" | generalise (one panel per layer with copper) | 5c |
| `pcbkit/check/copper.py:286-300` | `ipc2221_rise(external=True)` always external | test (callers pass `external`) | 5c |
| `pcbkit/check/coupling.py:1-20,107,120,128-163` | "no solid return plane" model; one `h_board` | change for 4L (decision D6) | 5c |
| `pcbkit/check/coupling.py:251,338` | fields on F/B; "other" layer is the opposite outer | generalise | 5c |
| `pcbkit/check/stitching.py:94-124` | `stitching_gaps`: F and B fills; `KeyError` if one is missing | generalise | 5b |
| `pcbkit/check/reserved.py:22,53,94-99` | layer names "top"/"bottom" only | generalise | 5b |
| `pcbkit/check/builtin/test_copper.py:6-8,97-99,144-166,300-364` | reserved layer words, stitching "both layers", `h_board`, outer copper, external IPC rise | generalise | 5b, 5c |
| `pcbkit/check/builtin/test_fab.py:84-117` | via ring read on F_Cu | test (K9) | 5a |
| `pcbkit/check/builtin/test_fab.py:120-136` | stackup regex `[FB].Cu` against `copper_mm` | generalise (inner against `inner_copper_mm`) | 5a |
| `pcbkit/check/builtin/test_fab.py` (new) | no inner-layer limits | change | 5a |
| `pcbkit/check/builtin/test_outputs.py:29-39,99-105` | `LAYERS` has F_Cu, B_Cu only | generalise | 6 |
| `pcbkit/check/builtin/test_outputs.py:108-132` | every job-file copper equals `copper_mm` | generalise (outer and inner) | 6 |
| `pcbkit/check/plugin.py:469` | results JSON has `copper_mm` | change for 4L only (key added only when layers = 4) | 6 |
| `pcbkit/kicad/cli.py:37-48` | `GERBER_LAYERS`: nine layers, two copper | generalise (function of the layer names) | 6 |
| `pcbkit/fab/gerbers.py:43-54` | `export` uses the default layer list | generalise | 6 |
| `pcbkit/fab/docs.py:37-51,108-121` | top and bottom copper PDFs only | change (inner PDFs on 4L) | 6 |
| `pcbkit/fab/pcbway.py:36-37,200-205,334-340,384-391,405` | "outer copper" constant; every copper entry compared with `copper_mm`; one "Copper weight" row | generalise (outer/inner split; 2L text unchanged) | 6 |
| `pcbkit/compare.py:22-32,58-59,331-337` | `COPPER_LAYERS = ("F.Cu","B.Cu")`; fixed id map | generalise (layers of the boards; refuse a layer-count mismatch) | 6 |
| `pcbkit/shots.py:35-42,53` | top/bottom SVG layer sets | test (outer views stay right); inner views optional (Q6) | 6 |
| `pcbkit/silk.py` | none | test only (vias through planes do not change silk) | 8 |
| `pcbkit/cli.py:70,551` | docstrings: "two-layer board", "F.Cu and B.Cu" | change wording | 7 |
| `pcbkit/scaffold.py:272` | template described as two-layer (true of the template) | test | 7 |
| `pcbkit/templates/board/pcbkit.toml` `[stackup]` | `layers = 2` | unchanged (template stays 2L); comment line naming 4 | 7 |
| `pcbkit/templates/board/routing.py:31-39` | GND on F and B | unchanged (2L template) | - |
| `docs/project-interface.md:60-61,92-94,101,305-314,371-391,476-478,521,596,706,721,822-848,941-995,1014,1266` | schema, stitching, helpers, stackup text, outputs, quote, compare, shots | change | 1 (schema table), 7 (rest) |
| `docs/concepts.md:41,44,100` | placement, post and copper rows say "both layers" | change | 7 |
| `docs/routing-recipes.md:49-124` | layer change, ground on both layers | change (add a four-layer recipe) | 7 |
| `docs/ordering.md:32,103,125-126,176,210` | two copper PDFs, "Layers 2", copper row | change | 7 |
| `docs/getting-started.md:180` | "both layers" | test (stays true for the 2L example) | 7 |
| `skills/new-board/SKILL.md:28` | "pcbkit builds two-layer boards only" | change | 1 (interim line), 7 (final) |
| `tests/unit/test_project.py:317` | row `("stackup","layers",4,"must be 2, got 4 ...")` | change: becomes `3`, "must be 2 or 4, got 3"; new rows added | 1 |
| `tests/unit/test_plugin.py:1259-1261` | `choices == (2,)`, skill line | change with the skill line | 1, 7 |
| `tests/unit/test_kicad_board_pure.py:308-328` | two `[FB].Cu` layers; inner layers left alone | test (stays true; inner setter gets its own tests) | 2 |
| `tests/unit/test_place.py:228-261` | `stackup_text` golden strings (`ONE_OUNCE`) | must stay byte-identical | 2 |
| `tests/unit/test_check_reserved_layer.py:10-19` | message "layer must be 'top' or 'bottom'" | generalise message on 4L only; 2L message kept | 5b |
| `tests/unit/test_fab_pcbway.py:133`, `tests/fab_files.py:49-59` | job file fixture defaults to 2 layers | extend (4L job fixture) | 6 |
| `tests/fake_pcbnew.py:24-25,261-262,233-256` | only F_Cu, B_Cu; no In1/In2, no layer types | extend | 2 |
| `tests/route_project.py:150-180`, `tests/check_boards.py:114-118`, `tests/check_group_boards.py:91` | two-layer stackup and builders | unchanged; new four-layer fixtures beside them | 2, 5b |
| `tests/integration/test_route_stages.py:271-372` | "ground pour on each layer", "two-layer ground" | unchanged; four-layer twins added | 3, 4 |
| `pcbkit/templates/board/golden/*` | 2L goldens (DSN, SES, prerouted) | must stay byte-identical | all |

## Decisions

Each decision lists the options weighed and a recommendation. **Sign-off** marks Bruk's
call.

### D1. Which layer counts to accept (sign-off)

- (a) 2 and 4 only. (b) Any even count. (c) 2, 4 and 6.
- **Recommend (a)**, with code written over an ordered tuple of copper layers rather than
  over four names, so allowing 6 later is a schema change plus tests. Six layers bring
  their own unknowns (two signal pairs, more stack presets, PCBWay cost) that nothing in
  hand needs.
- Until slice 8 lands, `place_board` and `pre` stop a four-layer project with "four-layer
  boards are not finished yet (WP15)". The schema can then accept 4 in slice 1 without
  letting anyone build half a board.

### D2. How a board declares its inner layers, and where (sign-off)

- (a) All in `routing.py`: the `zones` hook draws inner zones as it draws the GND pours
  today. This is flexible, but the zones arrive after Freerouting (`post`), so the DSN
  has no planes and the checks cannot tell a plane from a signal layer without importing
  Python.
- (b) All in `pcbkit.toml`: one net per inner layer. This is declarative and validated,
  and the fab code and checks can read it, but it cannot express a split power plane
  (several rails on one layer).
- (c) **Recommended, a hybrid.** `pcbkit.toml` declares each inner layer's role.
  `routing.py` draws only the geometry that needs code:

```toml
[stackup]
layers = 4
thickness_mm = 1.6
copper_mm = 0.035            # outer copper
inner_copper_mm = 0.035      # four-layer only
prepreg_mm = 0.2             # outer copper to the next inner layer; four-layer only
inner = [
  { role = "plane", net = "GND" },   # In1.Cu
  { role = "split" },                # In2.Cu: routing.py's planes hook draws it
]
```

  - `plane`: pcbkit draws one zone of `net` over the whole board, inset like the
    template's pours, in `pre`. KiCad layer type is `power`.
  - `split`: a power layer whose zones (several nets, cut-outs) come from a new optional
    `routing.py` hook, `planes(board, api)`. It runs in `pre` after `keepouts` and before
    the DSN. KiCad type is `power`.
  - `signal`: an ordinary routing layer that Freerouting may use. KiCad type is `signal`.
    Zones on it are optional, through `zones`.
- Validation in `parse_config`, after the per-key pass:
  - `inner` must have `layers - 2` entries.
  - `inner_copper_mm`, `prepreg_mm` and `inner` given on a two-layer board are an error
    that names the key. Defaults must not let a typo pass silently.
  - `role` must be one of the three; `net` is required for `plane` and refused for the
    others.
  - The core worked out from the stack must be at least 0.1 mm.
- A `plane` net that is not in the netlist stops `pre` and names the net.
- This needs a new Rule kind, `layers` ("list of tables" in
  `tests/unit/test_docs.py:25-33` `TYPE_WORDS`), and a cross-key step. Both are new to
  `project.py`.

### D3. How planes are made and how the autorouter is kept off them (sign-off after the spike)

- Planes are zones created in `pre`, so they are in `prerouted.kicad_pcb`, `golden/` and
  the DSN, and `finalize` rebuilds them from `golden/` as it does everything else.
- Keeping Freerouting off a plane layer, belt and braces:
  - KiCad layer type `power`, which should export as DSN `(type power)` (K5).
  - A rule area on each plane layer that bans tracks but allows vias and pours. It
    exports as a DSN keep-out, and KiCad's DRC catches any track that lands there.
- What happens to plane nets is the open question. Three options:
  - (a) KiCad exports the zones as DSN `(plane ...)` and Freerouting connects plane-net
    pads by dropping vias into them. Fewest moving parts, if it works (K5, K6).
  - (b) pcbkit fans out by itself in `pre`: for each SMD pad of a plane net, a locked via
    and a short locked track at a spot `clear_spot` finds. Plane nets then go into a net
    class Freerouting is told to ignore (`-inc`, K7). This is deterministic and testable,
    but adds a pass and a new hazard (fan-out vias in the way of the route).
  - (c) Leave plane nets to Freerouting as tracks. Rejected: it wastes outer-layer area
    and never uses the plane.
- **Recommend: the spike decides between (a) and (b).** Build (a) if K6 shows Freerouting
  connects to planes without stalls. Otherwise build (b). Slice 3 holds the hook and
  plane creation either way; only the fan-out sub-step (3b) depends on the answer.

### D4. Vias and stitching on four layers (sign-off)

- **Through vias only.** No blind, buried or micro vias: cost at PCBWay, and the SES and
  drill export stay as they are (K11). Default via sizes stay `rules.py:24-27`.
- **Stitching ties the GND layers together, whichever they are.**
  - `ground_pours` becomes "the GND zone on each copper layer that has one, top to
    bottom". Stitching needs at least two.
  - The grid and `fill_gaps` place a via where every GND layer is solid around the probe
    ring. For two layers that is exactly today's "both pours" rule, in the same order.
  - On signal - GND - power - signal with GND poured on both outer layers, that ties F,
    In1 and B. With no outer GND pours there is one GND layer and stitching does nothing,
    which is correct.
  - `_drop_orphan_vias` and the stitching check use the same "every GND layer" rule.
- **Cost to the power plane.** Every through GND via punches an antipad in In2. The
  copper solver sees this because it rasterises the filled zones. `[stitch] pitch_mm` and
  `dense` stay the levers. No extra rule now; recorded as risk R5.
- **`drop_one_sided_vias` counts every copper layer** (zones and pads on In1/In2 too). As
  written, a via from an outer pad to an inner power plane has copper on one outer layer
  only and would be removed. See R3.
- **eco skips copying copper on every plane net, not only GND** (sign-off). The reason it
  skips GND, Freerouting "normalization" stalls on copied fragments, plausibly applies to
  plane nets. Two-layer boards have no plane nets, so their behaviour is unchanged.

### D5. The copper current solver on planes (sign-off on the heating rule)

- **Layers:** every copper layer that has copper of the net, top to bottom.
- **Thickness per layer:** `copper_mm` outer, `inner_copper_mm` inner.
- **Joints:** a via or plated hole joins each pair of adjacent layers it passes through.
  Conductance comes from the barrel over that pair's spacing, taken from the stackup
  model. On two layers the existing joint (front to back, `1.6e-3`) is kept exactly, so
  two-layer numbers do not move.
- **Heating, recommended:** judge the IPC-2221 rise per layer, with `external=True` on
  outer layers and `external=False` (k = 0.024) on inner ones, using that layer's 99.5th
  percentile current per width. The worst layer is the result. Two layers then match
  today's single figure exactly only if the per-layer worst equals the pooled 99.5th
  percentile. **It may not**, so slice 5c keeps the pooled figure for two layers and uses
  per-layer only on four. Sign-off: IPC-2221 width-based rise on a plane is conservative
  and crude. The alternative is to report plane rise for information only and judge drop
  alone on plane layers.
- **Heatmap:** one panel per layer with copper. Two layers keep the same figure and
  titles.
- **Size:** a full-board GND net on three layers at `res=0.1` mm is about 1.5 times
  today's unknowns. Slice 5c measures it (K16) and may expose `res` per segment in
  `COPPER_SEGMENTS`.

### D6. The coupling model with planes (sign-off)

- **Where the reference height is worked out:** in the coupling slice (5c), from layer
  roles. `pcbkit/stackup.py` reports geometry only (`nearest_copper(layer)`,
  `gap_to_nearest_copper(layer)`); it does not say which layer is a reference.
- Today, by design: no solid return plane; `h = thickness_mm`; `d_near = 3h`; the far
  layer coupled through `hypot(gap, h)` (`coupling.py:1-20,322-345`).
- **Recommend, for four layers only:**
  - Each copper layer has a reference height. For a layer next to a `plane` or `split`
    layer it is the dielectric between them (`prepreg_mm` for outer layers). Otherwise it
    is `thickness_mm` as today. `d_near` and the 3H weight use the victim layer's height.
  - Copper on the far side of a solid plane layer does not couple. "Solid" means the
    plane's fill under the victim step; a step over a gap falls back to the far-layer
    formula. That way a gap is never treated as shielding.
  - Plane-layer zones of an aggressor net are not aggressor copper by default (sign-off).
    A high-current power plane under every trace would otherwise fail everything, and
    spread plane current is not a concentrated aggressor. Tracks and pads of the net
    still count. A board can set `planes_aggress = True` in `COUPLING` to opt in.
  - Keep Grover's free-space mutual inductance for now. It overestimates coupling over a
    plane, so it errs toward failing. An image-current formula is a later refinement
    (Q4).
- **Two-layer boards:** `from_spec(spec, h_board)` keeps its signature and result. The
  per-layer heights default to `{F: h_board, B: h_board}` and the shielding rule never
  fires.

### D7. Coordinates, layer names and stackup thickness (sign-off on the stack sum)

- **Coordinates are unchanged.** Layout mm from the top-left corner, KiCad file mm offset
  by (50, 50) (`board.py:41-43`). Nothing about four layers touches them.
- **Layer names:**
  - pcbnew ids by symbol only (`pcbnew.In1_Cu`), never by number.
  - Spec tables use "top", "in1", "in2", "bottom" (`RESERVED_COPPER`, future checks).
  - KiCad names ("In1.Cu") in `keepout(layers=...)`, as today.
  - A new helper, `kb.copper_layers(board)`, returns the ids top to bottom:
    `(F_Cu, B_Cu)` on two layers, in that order, so every generalised loop visits layers
    as today.
- **Stack thickness:**
  - The two-layer stack keeps its 0.07 mm short quirk byte for byte (`place.py:56-60`).
  - The four-layer stack is symmetric: F.Cu, prepreg, In1, core, In2, prepreg, B.Cu. The
    core is the remainder, so copper plus dielectric adds up to exactly `thickness_mm`,
    with masks outside the sum. Sign-off: whether the sum should include the masks
    depends on what KiCad does with it (K4) and what PCBWay quotes (K18).
- **Pure model:** a new `pcbkit/stackup.py` (pure, no pcbnew) builds the layer list,
  roles, thicknesses, z positions, `nearest_copper(layer)`,
  `gap_to_nearest_copper(layer)` and `span_mm(a, b)` from `StackupConfig`. Placement, the
  solver, coupling and the two follow-on checks all read it.
- **Geometry only:** the model does not decide which layer is a reference. D6's
  reference height (the dielectric to an adjacent `plane` or `split` layer, otherwise
  the board thickness) is worked out in slice 5c from layer roles, using the geometry
  here. A tie between equally near layers goes to the layer nearer the top of the stack.

### D8. Backwards compatibility

- **The rule:** with `layers = 2`, every file pcbkit writes is byte-identical to main,
  and every check result is the same apart from timing fields.
- How it is kept:
  1. Two-layer code paths are the existing constants and functions, not a general builder
     that happens to produce the same text. `stackup_text` for two layers is not routed
     through the four-layer builder.
  2. Generalised loops go through `kb.copper_layers`, which returns `(F_Cu, B_Cu)` in the
     current order. No set iteration where order reaches the board.
  3. New optional keys (results JSON, quote rows, Gerber layers, PDFs) appear only when
     `layers == 4`.
  4. The goldens that already pin bytes stay untouched: `tests/unit/test_place.py`
     stackup strings and `pcbkit/templates/board/golden/*`.
  5. A baseline is captured on Bruk's Mac before slice 1 merges (K0) and re-compared
     after slices 2, 3, 4 and 6.

## Slices

All code keeps the 3.9 floor. The gate after every slice is `uv run pytest tests/unit -q`,
`uv run --python 3.9 pytest tests/unit -q`,
`uv run ruff check . && uv run ruff format --check .`. Every new check or rule gets a
planted-mistake proof: plant the mistake, see the named test go red, remove
`__pycache__`, see it green. The reviewer gets the red output. "No KiCad" means a machine
where only the unit tests run; "Mac" means Bruk's machine with KiCad 10.0.6.

Order: 0 then 1 then 2, then {3, 4, 5a, 5b, 6} with the limits below, then 5c after 5b, 3b
after 3, then 7, then 8.

| After 2, in parallel | Shares no files or fixtures with |
|---|---|
| 3 (hooks, pre) | 4, 5a, 5b, 6 |
| 4 (post, stitch, cleanup, eco) | 3, 5a, 5b, 6 |
| 5a (fab limits) | 3, 4, 5b, 6 |
| 5b (stitching, reserved) | 3, 4, 5a, 6 |
| 6 (fab, compare, shots, outputs) | 3, 4, 5a, 5b |

Sequential: 5c after 5b (both edit `check/builtin/test_copper.py` and
`tests/check_boards_four.py`). 3b after 3 and the spike's D3 answer. 7 after 1-6. 8 last.
`tests/fake_pcbnew.py`, `pcbkit/kicad/board.py` and `tests/four_layer_project.py` are
owned by slice 2 and are read-only afterwards. A later slice that needs one of them
changed stops and asks.

### Slice 0: spike and baseline (Mac, Bruk; can run alongside slice 1)

- **Goal:** answer K1-K16 on one generic four-layer board, and capture the two-layer byte
  baseline (K0).
- **Touches:**
  - `tests/fixtures/four_layer/`: a saved board, a DSN, a SES, a job file and the Gerber
    file listing, from the generic board, renamed as CLAUDE.md asks.
  - `tests/fixtures/reports/drc_four_layer_*.rpt`.
  - New lines under "KiCad 10.0.6 facts" in `.claude/CLAUDE.md`.
  - The D3 answer appended to this plan.
- **Must not touch:** `pcbkit/`.
- **Acceptance:** each K item below has a recorded answer (the command and its output),
  and D3 is decided (a) or (b) with the evidence.
- **Verified without KiCad:** no.

### Slice 1: config and the stackup model (pure)

- **Goal:** `pcbkit.toml` accepts and validates a four-layer stackup (D1, D2), and
  `pcbkit/stackup.py` turns it into the layer model (D7). Nothing downstream builds four
  layers yet: the interim guard stops `place_board` and `pre`.
- **May touch:**
  - `pcbkit/project.py`.
  - `pcbkit/stackup.py` (new).
  - `pcbkit/place.py` and `pcbkit/route/pre.py`: only the one-line guard call at the top
    of `place_board` and `pre`.
  - `docs/project-interface.md`: the `[stackup]` rows and example only.
  - `tests/unit/test_project.py`, `tests/unit/test_docs.py` (`TYPE_WORDS`),
    `tests/unit/test_stackup.py` (new).
  - `skills/new-board/SKILL.md:28`, with its interim line, "Four-layer boards are in
    progress (WP15); build two-layer boards for now."
  - `tests/unit/test_plugin.py:1259-1261`.
- **Must not touch:** `kicad/board.py`, `route/*` apart from the guard line, `check/*`,
  `fab/*`, fixtures.
- **Acceptance:**
  - A two-layer `pcbkit.toml` gives a `Config` equal to main's.
  - The old row `("stackup","layers",4,...)` is replaced by `layers = 3` with "must be 2
    or 4, got 3", plus new rows for each D2 validation error (inner count, keys on two
    layers, bad role, missing or unexpected net, core under 0.1 mm), each with its
    message.
  - `stackup.py` tests show, for a 1.6 mm board with 0.2 mm prepreg and 1 oz everywhere:
    layer order, z positions, a core that makes the sum exactly 1.6,
    `gap_to_nearest_copper("top") == 0.2`, and `nearest_copper("bottom") == "in2"`.
  - Every schema key is documented: the existing docs test passes with the new kind.
- **Planted mistakes:**
  - Drop the inner-count check: its row goes red.
  - Let `inner_copper_mm` pass on two layers: its row goes red.
  - Change the core formula sign: the sum test goes red.
- **Verified without KiCad:** yes, fully.

### Slice 2: the board knows its layers

- **Goal:**
  - Placement writes a four-layer board: layer count, inner layer types from the roles,
    and the four-layer stackup text.
  - `set_copper` gains an inner counterpart (`set_inner_copper`, `In\d+.Cu` only); `post`
    calls it in slice 4.
  - `kb.copper_layers(board)`, `kb.layer_id(board, "in1")` and a `kb.plane_zone(...)`
    helper (a full zone with plane defaults, solid via connection where K9 says it is
    needed).
  - `clear_spot` collides on every copper layer.
  - The fake learns `In1_Cu`, `In2_Cu`, layer types and `GetCopperLayerCount`.
  - A generic four-layer test project (`tests/four_layer_project.py`, `my-board`, six
    parts) for slices 3-8.
- **May touch:** `pcbkit/place.py`, `pcbkit/kicad/board.py`, `tests/fake_pcbnew.py`,
  `tests/four_layer_project.py` (new), `tests/unit/test_place.py` (additions only),
  `tests/unit/test_kicad_board.py`, `tests/unit/test_kicad_board_pure.py` (additions
  only), `tests/integration/test_place_real_kicad.py` (four-layer twins,
  `@pytest.mark.kicad`).
- **Must not touch:** `route/`, `check/`, `fab/`; the existing `ONE_OUNCE` strings in
  `test_place.py`.
- **Acceptance:**
  - `stackup_text(0.035, 1.6)` still equals `ONE_OUNCE` (existing test, unchanged).
  - The four-layer stackup text matches a new golden string whose numbers come from
    `stackup.py`.
  - `set_inner_copper_text` rewrites `In1.Cu`/`In2.Cu` only, raises on a count that does
    not match the layers, and leaves outer copper alone (pure tests).
  - `copper_layers` on a two-layer fake returns `(F_Cu, B_Cu)`.
  - On the Mac: a placed four-layer board reloads with four copper layers, In1/In2 typed
    as the roles say, and the stackup thicknesses as written (K2, K3).
- **Planted mistakes:**
  - Make `set_inner_copper_text` also touch `F.Cu`: its test goes red.
  - Return `(B_Cu, F_Cu)` from `copper_layers`: the order test goes red.
- **Verified without KiCad:** pure parts yes; placement only on the Mac.

### Slice 3: planes before the router

- **Goal:**
  - `routing.py` gains the optional `planes(board, api)` hook.
  - `pre` creates the `plane` zones from the config, runs `planes`, adds the
    track-banning rule area on each plane layer, puts the edge strips on every copper
    layer, and checks that each `plane` net exists.
  - A two-layer `pre` is unchanged.
- **May touch:** `pcbkit/route/hooks.py`, `pcbkit/route/pre.py`,
  `tests/unit/test_route_hooks*.py` and `test_route_pre*.py` (or the existing modules for
  these stages), `tests/integration/test_route_stages.py` (new four-layer tests only,
  marked kicad).
- **Must not touch:** `kicad/board.py`, `route/post.py`, `stitch.py`, `cleanup.py`,
  `eco.py`, the existing tests' bodies.
- **Acceptance:**
  - Hooks: a `planes` that is not callable is a `ProjectError` naming it.
  - Order (fake pcbnew): `prerouted`, `keepouts`, plane zones, `planes`, edge strips.
  - A `split` layer with no `planes` hook stops `pre` with a message.
  - On the Mac: the DSN has In1/In2 typed as expected and plane entries as K5 found them;
    the two-layer blinky prerouted board and DSN match the K0 baseline byte for byte.
- **Planted mistakes:**
  - Move the plane step after `export_dsn`: the order test goes red.
  - Drop the plane-layer track ban: its test goes red.
- **Verified without KiCad:** order and validation yes; DSN contents only on the Mac.

### Slice 3b: plane-net connections (after slice 0 decides D3)

- **Goal:** for (a), nothing to build beyond checking that a four-layer route connects
  every plane-net pad (DRC unconnected = 0). For (b), a fan-out pass in `pre` and the
  net-class ignore in the Freerouting command line.
- **May touch:** `pcbkit/route/pre.py`, `pcbkit/route/freerouting.py` (for (b)), their
  unit tests, `tests/e2e/` (a four-layer case).
- **Acceptance:** (b) only: each SMD pad on a plane net gets one locked via within
  `reach`, with no DRC clearance error, and the Freerouting command includes the ignore
  flag only when the board has plane nets (unit test on `build_command`). E2E on the Mac:
  four-layer DRC clean, with 0 unconnected.
- **Planted mistake:** (b) skip one pad: the coverage test goes red.
- **Verified without KiCad:** command-line and choice logic only.

### Slice 4: post, stitching, clean-up and eco over all layers

- **Goal:** D4.
  - `ground_pours` returns every GND layer's pour; `stitch`, `fill_gaps` and
    `_drop_orphan_vias` use "every GND layer"; `fill_gaps`' second pass collides on every
    layer.
  - `drop_one_sided_vias` counts zones and pads on every copper layer.
  - `eco`'s `clashes` checks every copper layer and skips copying plane-net copper.
  - `post` writes the inner copper after the outer.
- **May touch:** `pcbkit/route/post.py`, `stitch.py`, `cleanup.py`, `eco.py`,
  `tests/unit/test_route_*` for these, `tests/integration/test_route_stages.py`
  (four-layer twins only).
- **Must not touch:** `kicad/board.py`, `pre.py`, `hooks.py`.
- **Acceptance:**
  - Pure: the stitch probe rule as a function of a set of fills gives the same result as
    before for two fills.
  - On the Mac:
    - A four-layer post keeps a via from an outer pad to the In2 plane. A one-sided via
      with no plane still goes.
    - Stitching vias on a board with GND on F, In1 and B land only where all three are
      solid.
    - `finalize` from the blinky golden matches the K0 baseline byte for byte.
- **Planted mistakes:**
  - Revert `drop_one_sided_vias` to F/B: the four-layer keep test goes red. This is the
    R3 bug, so this proof matters most.
  - Use "any GND layer" in place of "every": the stitch test goes red.
- **Verified without KiCad:** pure parts; the rest on the Mac.

### Slice 5a: inner-layer fab limits

- **Goal:**
  - On four-layer boards, `test_fab` checks the inner copper thickness against
    `inner_copper_mm`.
  - New optional `PCBWAY` keys: `min_track_inner`, `min_space_inner`,
    `min_annular_inner`, `inner_hole_clearance`. The design rules and the via rings on
    inner layers must meet them.
  - A missing key fails on four layers, naming it, as the module already does. Two-layer
    boards never read them.
- **May touch:** `pcbkit/check/builtin/test_fab.py`, `docs/project-interface.md` (the
  `fab` specs rows only), `tests/integration/test_check_fab_group.py` (four-layer twins).
- **Must not touch:** the template `specs.py` (it is two-layer),
  `tests/check_group_boards.py` (add `tests/check_group_boards_four.py` if needed).
- **Acceptance:** a four-layer board with inner copper not equal to `inner_copper_mm`
  fails, and so does one with the design-rule hole clearance under
  `inner_hole_clearance`. The two-layer fab group results are unchanged.
- **Planted mistake:** compare inner copper against `copper_mm`: the 0.5 oz inner test
  goes red.
- **Verified without KiCad:** no (needs pcbnew); the docs rows yes.

### Slice 5b: stitching and reserved-region checks

- **Goal:**
  - `stitching_gaps` samples points that are in every GND layer's fill. A board with
    fewer than two GND layers skips with the reason "no two GND layers to stitch", which
    is a fact about the board and not a loosened check.
  - `reserved` accepts "in1"/"in2" on four-layer boards. On a two-layer board, "in1" is
    an error naming the board's layers, and the existing message for typos stays.
- **May touch:** `pcbkit/check/stitching.py`, `pcbkit/check/reserved.py`,
  `pcbkit/check/builtin/test_copper.py` (the reserved and stitching parts and its
  docstring), `tests/unit/test_check_reserved_layer.py` (additions),
  `tests/unit/test_check_stitching_pure.py`, `tests/check_boards_four.py` (new),
  `tests/integration/test_check_copper_group.py` (four-layer twins).
- **Must not touch:** `copper.py`, `coupling.py`.
- **Acceptance:**
  - The existing stitching density tests (`test_check_copper_group.py:399-510`) pass
    unchanged.
  - A four-layer board missing vias in one block fails density.
  - A track on In2 inside an "in2" reserved box is reported.
- **Planted mistake:** sample "any GND layer": the four-layer block test goes red.
- **Verified without KiCad:** the pure parts and the layer-name validation.

### Slice 5c: copper solver and coupling (after 5b)

- **Goal:** D5 and D6. D6's reference height is worked out here, from layer roles; the
  stackup model only reports geometry (`nearest_copper`, `gap_to_nearest_copper`).
- **May touch:** `pcbkit/check/copper.py`, `pcbkit/check/coupling.py`,
  `pcbkit/check/builtin/test_copper.py` (`_config`, `_solve`, segment and regulation
  tests), `tests/unit/test_check_copper_pure.py`,
  `tests/unit/test_check_coupling_pure.py`, `tests/check_boards_four.py`,
  `tests/integration/test_check_copper_group.py` and `test_check_engines.py` (four-layer
  twins).
- **Must not touch:** the two-layer expectations in those integration tests.
- **Acceptance:**
  - Two-layer solver drops and losses equal main's to the last recorded digit on the
    existing boards (`check_boards.power_tracks`).
  - Four layers:
    - A plane path is several times lower in resistance than the same net as an outer
      track.
    - A via chain's resistance grows with the span.
    - An inner layer is judged with k = 0.024.
  - Coupling:
    - A trace over a solid plane passes where the same geometry with no plane fails.
    - A trace over a slot in the plane is judged as no plane.
    - Two-layer results are unchanged.
  - The solver run time on the four-layer fixture is recorded (K16).
- **Planted mistakes:**
  - Use `external=True` on inner layers: the inner rise test goes red.
  - Treat a plane gap as shielding: the slot test goes red.
  - Drop the joint between In1 and In2: the via-chain test goes red.
- **Verified without KiCad:** pure formulas (heights, spans, Lmax, IPC); the solver and
  fields only on the Mac.

### Slice 6: fab outputs, quote, compare, shots

- **Goal:**
  - The Gerber layer list comes from the board's layer names; four-layer exports add
    In1.Cu and In2.Cu.
  - `test_outputs` expects them and checks outer and inner copper in the job file
    separately.
  - Inner copper PDFs on four layers (`<fab>_in1_copper.pdf`, `<fab>_in2_copper.pdf`).
  - The quote shows an "Inner copper" row on four layers, with the warning comparing
    outer and inner separately. The two-layer quote text is unchanged to the byte.
  - `compare` compares every copper layer of the boards and refuses (exit 2) two boards
    with different layer counts.
  - Results JSON gets `inner_copper_mm` only on four layers.
  - Shots: outer views unchanged; inner views per Q6.
- **May touch:** `pcbkit/kicad/cli.py`, `pcbkit/fab/gerbers.py`, `pcbkit/fab/docs.py`,
  `pcbkit/fab/pcbway.py`, `pcbkit/compare.py`, `pcbkit/shots.py`,
  `pcbkit/check/plugin.py` (line 469 only), `pcbkit/check/builtin/test_outputs.py`,
  `tests/fab_files.py`, `tests/unit/test_fab_*`, `test_compare*`, `test_shots.py`,
  `test_kicad_cli*.py`, `tests/integration/test_fab_real_kicad.py`,
  `test_compare_real_kicad.py`, `test_check_outputs_group.py` (four-layer twins).
- **Must not touch:** route, place, check engines.
- **Acceptance:**
  - `format_quote` on the existing two-layer fixture is byte-identical.
  - Read from a four-layer job file fixture (K10), the quote shows "Layers 4", the outer
    and inner copper, and warns when inner copper differs from `inner_copper_mm`.
  - The default two-layer `GERBER_LAYERS` call is unchanged.
  - `compare` of a two-layer and a four-layer board exits 2 with a message.
- **Planted mistakes:**
  - Count inner copper as outer in the quote: the outer/inner test goes red.
  - Drop In2.Cu from the four-layer export list: the gerber-set test goes red.
- **Verified without KiCad:** quote, layer lists and compare's pure parts yes (job file
  fixture from slice 0); exports on the Mac.

### Slice 7: docs and skills

- **Goal:**
  - `project-interface.md`: the `planes` hook, helpers, what the engine assumes, stackup
    text, outputs, quote, compare, shots.
  - `concepts.md`, `routing-recipes.md` (a "four layers: signal, ground, power, signal"
    recipe, generic), `ordering.md`.
  - `skills/new-board/SKILL.md` (final wording) and the `cli.py` docstrings.
  - CHANGELOG.
- **May touch:** `docs/*`, `skills/*`, `pcbkit/cli.py` (docstrings only), `CHANGELOG.md`,
  `tests/unit/test_docs*.py` and `test_plugin.py` (assertions that follow the docs).
- **Acceptance:** the docs tests pass, including the example `routing.py`, which runs
  against the documented helpers. No column-0 `#` in Python examples. Generic names only;
  plain hyphens.
- **Verified without KiCad:** yes.

### Slice 8: end to end, and lift the guard

- **Goal:**
  - Remove the WP15 guard.
  - Kicad-marked integration tests on `tests/four_layer_project.py`: build, pre, post
    (from a committed generic SES), check (kicad, outputs, copper, fab), finalize, quote.
  - An e2e test with real Freerouting.
  - Re-run the K0 baseline diff on blinky and on Bruk's own two-layer boards.
- **May touch:** `pcbkit/place.py`, `pcbkit/route/pre.py` (guard lines only),
  `tests/integration/*four*`, `tests/e2e/*four*`.
- **Acceptance:** all kicad and e2e tests green on the Mac, the K0 diff empty, DRC on the
  four-layer fixture clean with 0 unconnected.
- **Verified without KiCad:** no.

## Needs real KiCad (Bruk's Mac, KiCad 10.0.6, `.venv-kicad`)

Each item gives the experiment and the answer the plan assumes until it is measured.

| # | Fact | Experiment | Assumed meanwhile |
|---|---|---|---|
| K0 | Two-layer byte baseline | On main, in a fresh `pcbkit new my-board`: `pcbkit build`, then run `pre` alone (`.venv-kicad/bin/python -c "from pcbkit.project import load_project; from pcbkit.route.pre import pre; pre(load_project('.'))"`), then `pcbkit finalize`, `pcbkit check`, `pcbkit quote`. Save `kicad/*.kicad_pcb`, `kicad/*.dsn`, `out/fab/gerbers/*` with creation-date lines removed, the quote text, and the results JSON without `seconds`. Do the same on your own two-layer boards (not committed). | Byte-identical after every slice |
| K1 | Inner layer ids and names | `b=pcbnew.NewBoard('/tmp/f.kicad_pcb'); b.SetCopperLayerCount(4); print(pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu, [b.GetLayerName(l) for l in (pcbnew.In1_Cu, pcbnew.In2_Cu)])` | Ids 0, 4, 6, 2 (matching the fake's `B_CU = 2`); names "In1.Cu", "In2.Cu". Code uses the symbols only |
| K2 | Layer type setter and how it is saved | Continue K1: `b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)`, `pcbnew.SaveBoard(...)`, then `grep -n 'In[12].Cu' /tmp/f.kicad_pcb` | `SetLayerType` exists and writes `power` in the `(layers ...)` section |
| K3 | A hand-inserted four-layer stackup survives LoadBoard and SaveBoard | Insert the slice 2 text after `(setup`, `LoadBoard`, `SaveBoard` to a second file, diff the stackup blocks | Kept with its order, types (`prepreg`, `core`) and thicknesses, in the multi-line form `set_copper_text` already matches |
| K4 | Board thickness against the stackup sum | Make the sum differ from `SetBoardThickness` by 0.1 mm; read the job file's `BoardThickness` and look for a DRC warning | The job file reports `SetBoardThickness`; no DRC complaint |
| K5 | DSN export with inner zones | Four-layer board, a GND zone on In1 (unfilled and filled), a +5V zone on In2; `ExportSpecctraDSN`; `grep -n "layer In\|type power\|plane\|Via\[" f.dsn` | `(type power)` on both inner layers; each zone a `(plane NET (polygon In1.Cu ...))` from its outline; the via padstack `Via[0-3]_700:300_um` |
| K6 | Freerouting 1.9.0 with plane layers | Run pcbkit's command (`build_command`) on the K5 DSN of a six-part board with plane-net SMD pads; `ImportSpecctraSES`; DRC unconnected count; read the log for "normalization" | No tracks on power layers; plane-net pads connected with vias; no stall; same route on repeat. If not, D3 goes to (b) |
| K7 | Freerouting ignores a net class from the command line | `java -jar freerouting.jar -help`, then a run with `-inc <class>` | `-inc` exists in 1.9.0 (needed for D3(b) only) |
| K8 | SES import on four layers | Import K6's SES; count vias and their layer pair (`v.TopLayer()`, `v.BottomLayer()`) | Through vias F to B; zones untouched |
| K9 | Zone fill on inner planes | Fill K5; for a GND via and a +5V via, print `IsOnLayer(In1_Cu)`, `GetWidth(In1_Cu)`, and whether the In1 fill joins the GND via solid or with spokes; check KiCad 10's padstack "unconnected layer" default for vias | Vias join zones solid; PTH pads get thermals per zone setting; vias keep their ring on every layer (no removal of unconnected rings by default) |
| K10 | Gerber names and job file on four layers | `kicad-cli pcb export gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,... --no-protel-ext` into an empty folder; `ls`; read `*-job.gbrjob` | Files `<stem>-In1_Cu.gbr`, `<stem>-In2_Cu.gbr`; `LayerNumber: 4`; `MaterialStackup` entries with `Name` "In1.Cu", `Type` "Copper", `Thickness`, plus dielectric entries |
| K11 | Drill export with through vias only | `kicad-cli pcb export drill ...` on the four-layer board; `ls` | Still `-PTH.drl` and `-NPTH.drl`; no layer-pair files |
| K12 | DRC report on four layers | `kicad-cli pcb drc` on a four-layer board with a planted inner clearance error; save the report as a fixture | Same format; items say "on In1.Cu"; `parse_drc` reads it unchanged |
| K13 | Polygon contours of inner layers (compare) | `board.ConvertBrdLayerToPolygonalContours(pcbnew.In1_Cu, ps)` on the filled board; `ps.Area()` | Works and includes zone fills |
| K14 | SVG and PDF export of inner layers | `kicad-cli pcb export svg --layers In1.Cu,Edge.Cuts` and `export pdf` likewise | Both work |
| K15 | Item shapes on inner layers | `via.GetEffectiveShape(pcbnew.In1_Cu)`, `pad.IsOnLayer(pcbnew.In1_Cu)` for a PTH and an SMD pad | Through items are on inner layers; SMD pads are not |
| K16 | Solver size and time | Time `NetCopper(board, "/GND")` and `solve` on the four-layer fixture with GND on three layers, at `res` 0.1 and 0.2 | Under a minute and a few GB at 0.1 on a 100 x 80 mm board; otherwise expose `res` |
| K17 | 3D render of a four-layer board | `pcbkit shots` render views | Unchanged |
| K18 | PCBWay four-layer facts (not KiCad) | From PCBWay's capability page, or ask them: standard 1.6 mm four-layer stack (prepreg and core), default inner copper, inner minimum track and space, inner annular ring, hole to inner copper, and what the order form asks for four layers | Prepreg 0.2 mm; inner 1 oz; inner track and space at the outer 2 oz limits; hole to inner copper 0.25 mm. **All unconfirmed**: slice 5a's defaults stay out of the template until confirmed |

## Risks, and what would make it bigger than it looks

Size by complexity, unknowns and risk: large. About 25 modules change. The pattern is
repetitive (loop over layers) but spread wide, and most of it can only be proved on the
Mac. **The unknowns dominate on routing (K5-K8):** they decide whether slice 3b is
nothing or a new fan-out pass with its own hazards. Hence the spike first. Slice 1 can
run during the spike because it depends on no KiCad fact.

- **R1. Freerouting and planes.** If 1.9.0 ignores DSN planes, routes plane nets as
  tracks, or stalls on them, D3 becomes (b): a deterministic fan-out pass plus a
  net-class ignore. That is one more stage with clearance hazards near fine-pitch parts.
  If `-inc` is missing too, pcbkit would have to edit the DSN text, which it has never
  done.
- **R2. Most acceptance is on the Mac.** The fake cannot fill zones, export DSNs or solve
  copper. Bruk's time becomes the bottleneck. Batch the Mac runs: after slice 2, after
  {3, 4, 6}, after 5c, and at 8.
- **R3. `drop_one_sided_vias` deletes plane connections.** As written
  (`cleanup.py:85-98`) it would remove every via that joins an outer pad to an inner
  power plane, and the board would still pass routing with those connections silently
  missing until DRC. Slice 4's planted mistake guards it. Nothing four-layer may run
  before slice 4.
- **R4. Byte identity can drift unnoticed.** Item order in a saved board follows the
  order of `board.Add`; a generalised loop that iterates a set, or adds plane zones on
  two-layer boards, changes bytes. D8's rules and the K0 diff are the guard. Only
  `test_place.py` and the template goldens pin bytes in CI.
- **R5. Power plane current.** Stitching antipads, thermal spokes and via fields cut a
  power plane carrying tens of amps. The solver models it only if it rasterises inner
  fills correctly (K9, K16). IPC-2221 on a plane is crude (D5 sign-off).
- **R6. Coupling physics.** D6 changes the model on four layers. If Bruk wants the
  image-current formula now, slice 5c grows into a modelling task with its own
  validation.
- **R7. Eco on four layers.** Copied plane-net vias may stall Freerouting the way copied
  GND fragments did. D4 skips them, which means eco re-routes every plane connection.
- **R8. DRC time.** Filling two full inner planes may push `TIMEOUT_DRC` (900 s) on a big
  board. Measure it in slice 8.
- **R9. KiCad stackup rewriting (K3).** If KiCad reorders or renames the inserted stack,
  `set_inner_copper_text` and the fab check regexes need the saved form, not the inserted
  one.
- **R10. PCBWay facts (K18)** are outside KiCad and may change the stack defaults, which
  would move every coupling and hot-loop number derived from `prepreg_mm`.

## Room for two follow-on checks (not built in WP15)

### Regulator hot-loop area

- **Needs:** for a buck's input loop (input capacitor, high-side switch input pin,
  low-side or ground return), the loop's area and its height over the first reference
  plane. Loop inductance over a plane is about mu0 x h x (path length / width), so
  `stackup.gap_to_nearest_copper(layer)` from slice 1 is the geometry input; the
  reference height itself comes from the roles (D6).
- **Room left:**
  - The pure `stackup.py` model exposes the nearest copper layer and the gap to it per
    copper layer (geometry only; roles decide the reference, D6).
  - The `copper` group's docstring and the specs naming leave a slot for `HOT_LOOPS` rows
    (`name`, `cap_ref`, `cap_pads`, `switch_ref`, `switch_pin`, `max_nh`).
  - `coupling.loop_area` (`coupling.py:343`) is the pattern to reuse for the area.
- **Do not build now:** the check needs a validated inductance model and a test board
  with a planted wide loop.

### A fast or high-current net crossing a gap in its reference plane

- **Needs:**
  - Per copper layer, its adjacent plane layer and that layer's fill polygons.
  - Per flagged net (`FAST_NETS` in specs), the track centrelines on that layer, sampled
    at a step.
  - Any run of samples over no fill, or over fill of a different net on a split layer, is
    a crossing.
  - A via changing reference planes without a nearby stitching or decoupling via is the
    same family of fault.
- **Room left:** `stackup.nearest_copper` (slice 1, geometry; the reference comes from the roles); `kb.copper_layers` and the
  per-layer fill access built in slices 4-5c; the "a step over a gap is not shielded"
  rule in D6 already samples the reference fill under a track, so the crossing check can
  share that sampler.
- **Do not build now.**

## Open questions for Bruk

- **Q1 (D1).** Accept 2 and 4 only?
- **Q2 (D2).** The `inner = [{ role, net }]` shape in `pcbkit.toml`, and the
  `planes(board, api)` hook name.
- **Q3 (D4).** Should eco skip copying copper on every plane net, as it skips GND?
- **Q4 (D6).** Treat the power plane as a reference? Exclude plane-layer aggressor copper
  by default? Keep Grover for now?
- **Q5.** The two-layer via barrel uses a fixed 1.6 mm (`copper.py:113-117`). Fix it in a
  later WP (it changes two-layer numbers), or leave it?
- **Q6.** Inner-layer shots (`pcbkit shots --side in1`), or are the inner copper PDFs
  enough?
- **Q7 (D7).** Should the four-layer stack sum include the solder masks?
