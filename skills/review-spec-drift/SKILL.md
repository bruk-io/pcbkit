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

## 1. Read the spec and write its claims down first

- The prose spec: ask where it is (a `README.md` or a spec file in the project, a doc
  elsewhere), and read all of it, captions, tables and notes included. design.py's
  `NOTES` and `COMMENT` are claims too.
- **Extract claims.** Number every claim. Tag each `mechanical` (decidable from the
  netlist, footprints, BOM or geometry) or `behavioural` (default states, enable chains,
  "X powers Y only", fail-safe claims). Exclusions and open questions are claims too.
- **Check the spec against itself.** Captions, diagrams and summaries lag the body. Two
  parts of the spec that disagree are a finding before the design is opened.
- **Write each mechanical claim down as the spec states it, before reading the
  design.** A claim [references/claim-homes.md](references/claim-homes.md) gives a
  table goes into that table in specs.py; a claim about what the design contains (a
  count, a footprint, the pads on a net, the outline, the stackup) goes into
  `spec-claims.json` in the project folder, `{fact key: claimed value}`, keyed as
  `listing.py` names its facts.

## 2. Let code compare them

Run only the checks whose inputs exist at the gate the board is at (`sch`, `place`,
`route` or, before an order, `order`); one whose inputs do not exist yet is not a
finding, and its claims are `cannot tell`. The script names any group pcbkit.toml leaves
off (switch it on in `[checks] groups`) and prints that gate's run with its caveats; run
what it prints, adding `or test_<topic>` for each project check whose inputs exist, then
the listing:

```
.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/board-workflow/scripts/groups.py" --gate sch|place|route|order
.venv/bin/python "${CLAUDE_SKILL_DIR}/scripts/listing.py" spec-claims.json
```

A claim whose check passed, or
that `listing.py` reports `matches`, matches; one whose check failed, or that it reports
`differs` or `not in design`, differs, with the message or both values as evidence.

## 3. Judge what code cannot

- **Behavioural claims**: which listed parts and nets each refers to (`listing.py`
  prints the pads on every net, so a chain is a lookup), and whether the chain does what
  the claim says.
- **What no claim covers**: every part and net in the listing that no claim names is
  `not in spec`: scope creep, or a spec never updated? When a part changed, re-check
  every claim that mentioned it and anything it fed.
- **Open questions**: does the design now answer each, silently assume an answer, or
  leave it open? Answered or assumed is `differs` until the spec records the answer.
- Never decide which side is right, and never edit the spec or the design during the
  review. Drift to hunt: a part swap that changed a rating in one place only; a
  consolidation that dropped a feature the spec still lists; a part that kept its name
  but changed its job; captions describing an earlier layout. A generic worked example,
  for the shape of the claims table: [references/example.md](references/example.md).

## 4. Settle the rows with the user

Ask the decisions below. Once the user has decided, change the row or the design to
match, so the next gate reads a result. A claim with no built-in check sits in a
proposed table (`PIN_NETS`, `PORT_EDGES`, `EXCLUSIONS`, `POWER_PATHS`, `I2C_EXPECTED`;
shapes in "Proposed tables", `${CLAUDE_PLUGIN_ROOT}/docs/project-interface.md`), each
handed to `add-check`. Behavioural claims stay in the prose spec until a check can hold
them; `walk-fault-states` writes the default-state ones as `FAULT_STATES`.

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

**Written down**: each claim's row in specs.py or `spec-claims.json`, and what changed
once the user decided (step 4).
