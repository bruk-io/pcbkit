---
description: >-
  Compare a pcbkit board as built (design.py, layout.py, pcbkit.toml, specs.py and the
  check results) against its written spec, report every disagreement in both
  directions, ask which side is stale, and move each claim a program can decide into
  the specs.py table or layout.py value a check reads. Load it when asked whether the
  design still matches its spec, README or port map, or when board-workflow reaches a
  gate (schematic, placement, order). Without it Claude checks only spec against
  design, misses parts the spec never mentions and open questions the design silently
  answered, trusts stale diagram captions, recounts by reading what the checks or a
  netlist listing already hold, and "fixes" one side itself instead of asking.
---

# Spec drift check

Spec and design drift apart because one gets edited and the other does not. This
review finds every place they disagree and asks the user which side is right. It does
not judge whether a claim is a good idea: ratings, current paths, fault states and plugs
have their own reviews. Gate: every gate. In pcbkit each claim a program can decide
lives in a `specs.py` table, a `layout.py` value or `pcbkit.toml`, where a check reads
it; the prose spec holds intent, behaviour and open questions.

## 1. Start from the checks that apply at this gate

Run only the checks whose inputs exist; a check whose inputs do not exist yet is not a
finding, and its claims are `cannot tell`:

- Schematic: `.venv/bin/pcbkit check -k "test_circuit or test_esp32s3 or test_erc_clean"`
- Placement: the same, plus `or test_decoupling_caps_are_close or
  test_power_footprints_match_datasheet or test_filter_caps_sit_by_the_adc_pin`
- Route: `.venv/bin/pcbkit check -k "not test_outputs"`
- Order: `.venv/bin/pcbkit check` with no `-k`.

Add `or test_<topic>` for each project check whose inputs exist. A `-k` run leaves
partial results; the order gate runs all. A gate option (WP17, --gate) will replace
these selections. For each claim type,
[references/claim-homes.md](references/claim-homes.md) says where it lives and which check
holds it: a claim whose check passed matches; one whose check failed differs, with the
check's message as evidence.

## 2. Read the spec and settle the claims

- The prose spec: ask where it is (a `README.md` or a spec file in the project, a doc
  elsewhere), and read all of it, captions, tables and notes included. design.py's
  `NOTES` and `COMMENT` are claims too.
- **Extract claims.** Number every claim. Tag each `mechanical` (decidable from the
  netlist, footprints, BOM or geometry) or `behavioural` (default states, enable chains,
  "X powers Y only", fail-safe claims). Exclusions and open questions are claims too.
- **Check the spec against itself.** Captions, diagrams and summaries lag the body. Two
  parts of the spec that disagree are a finding before the design is opened.

## 3. List the design by script, then match

What the design contains is listable, never recounted by reading. In the project's
`.venv/bin/python`, read the netlist with
`pcbkit.check.netlist.Netlist.from_file("kicad/<stem>.net")` and print:

- every connector, regulator, IC, switch, jumper and test point (`parts`, `kind(ref)`),
  and every `dnp` part from design.py;
- each enable net and each rail with the parts on it (`refs_on(net)`), so the chain
  from a source to its loads is a listing, not a trace in the conversation.

Quote the output. The judgement is the match: which listed item each claim refers to,
whether the chain does what a behavioural claim says, and every item no claim covers
(`not in spec`: scope creep, or a spec never updated?). When a part changed, re-check
every claim that mentioned it and anything it fed. For each open question, say whether
the design now answers it, silently assumed an answer, or leaves it open; answered or
assumed is `differs` until the spec records the answer.

Never decide which side is right, and never edit the spec or the design during the
review. Drift to hunt: a part swap that changed a rating in one place only; a
consolidation that quietly dropped a feature the spec still lists; a part that kept its
name but changed its job; captions describing an earlier layout. A generic worked
example, for the shape of the claims table: [references/example.md](references/example.md).

## 4. Move claims to code

Once the user has decided, write each settled mechanical claim where a check reads it,
so the next gate reads a result: `layout.W` and `H` for the outline size, `HOLES` for
the holes; `[stackup]` for layers and copper; `SUPPLY_PINS` for a rail at a load;
`I2C_BUSES` for bus devices; `PINOUT` for a part's pin functions; `DEVKIT_GPIO` and
`ANALOG_NETS` on an ESP32-S3 DevKit board. Claims with no built-in check go into the
proposed tables (`PIN_NETS`, `PORT_EDGES`, `EXCLUSIONS`, `POWER_PATHS`,
`I2C_EXPECTED`; shapes in "Proposed tables",
`${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`), each handed to `add-check`.
Behavioural claims stay in the prose spec until a check can hold them;
`walk-fault-states` writes the default-state ones as `FAULT_STATES`.

## Output

Always this format, nothing before it but one line naming the spec, the design files
and the gate.

**Claims**

| # | Claim | Type | Design evidence | Status |
|---|---|---|---|---|

Type is `mechanical` or `behavioural`. Design evidence names the check that settled it,
or the listing and the ref or net. Status is one of `matches`, `differs`, `missing in
design`, `not in spec`, `cannot tell`. Conflicts inside the spec are `differs`, with
both spec locations as evidence.

**Decisions needed**: one numbered question per mismatch, each with its two options
("J1 is a terminal block in the design and a barrel jack in the port map. Update the
spec, or change J1 back?"). Order by consequence: what changes the order first.

**Moves to code**: each mechanical claim settled by a listing, with the table or value
it goes into (step 4).
