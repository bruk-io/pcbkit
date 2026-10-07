# The Claude Code plugin

pcbkit ships a Claude Code plugin in the same repository: the repository root is the plugin
root. It teaches Claude the pcbkit workflow, keeps Claude's edits out of the files pcbkit
generates, and can fill in a PCBWay order, stopping at sign-in, at every upload and before
payment. It has five skills, two agents and two hooks, and no server of its own: everything
Claude does to a board is a `pcbkit` command you could type.

## Install

```
claude plugin marketplace add bruk-io/knowhere
claude plugin install pcbkit@knowhere
```

`knowhere` is the marketplace pcbkit is published to. At the time of writing it has no pcbkit
entry yet, so those two commands will not find the plugin until pcbkit is published. To try
the plugin before then, load it from a checkout of this repository. The flag lasts for that
one session:

```
claude --plugin-dir ~/src/pcbkit
```

The plugin needs three things around it:

- **pcbkit and a board project.** The plugin does not install pcbkit. Its skills run
  `pcbkit` commands, from the project's `.venv` where they need pcbnew (see
  [getting started](getting-started.md)).
- **`python3` on your PATH.** The hooks are Python scripts that use only the standard
  library, so they need no pcbkit and no KiCad.
- **Claude in Chrome**, for the browser steps of the order skill only.

## Skills

A skill is a set of instructions that Claude loads when your request matches the one-paragraph
description at the top of it. You can also run one by name, with the plugin's name in front:
`/pcbkit:board-workflow`.

| Skill | What it is for | Claude loads it when you... |
|---|---|---|
| `board-workflow` | The build, route, promote, finalize and check loop. What to run when, how to read DRC and check failures, eco route or full route, which file to change for what. | ask to build, place, route, finalize or export a board, fix DRC or ERC findings, understand a failing check, or ask which file to change, in a folder that has a `pcbkit.toml`. |
| `new-board` | Starting a board: it interviews you once, runs `pcbkit doctor`, `pcbkit new` and `pcbkit setup`, fills in `design.py`, `layout.py`, `specs.py` and `pcbkit.toml`, and gets the first `pcbkit build` clean. | ask to design or start a new board, or turn a circuit idea into a KiCad board, and there is no `pcbkit.toml` for it yet. |
| `add-check` | Writing a design check with a sourced limit, and proving it fails: a planted mistake in `mutants.py` for a circuit check, or in a scratch copy for a copper or board check. It never loosens a limit to get a green run. | say "add a check", "make sure X can never happen" or "test that this stays under that", or want a datasheet limit or a layout rule enforced. |
| `review-board` | A layout review from the pictures and the DRC report: signals under a switching regulator, pours cut into islands or thin necks, slivers. It returns findings only, most serious first and at most twelve, and edits nothing. | ask for a layout review or a second look at the routed board. With `/pcbkit:review-board`, anything you add after it (a region name, a concern) is the focus. |
| `order-pcbway` | Filling in a PCBWay order from `pcbkit quote`, stopping at sign-in, at every upload and before payment. See [ordering](ordering.md). | **Only when you start it:** `/pcbkit:order-pcbway 5 2` (boards to make, boards to assemble). Claude cannot start it on its own. |

Two of these behave differently from the rest:

- `review-board` runs in a forked conversation, because the renders and DRC listings it reads
  are noisy. You wait for it, and only its findings come back. It may run `pcbkit shots` and
  nothing else, and has no edit tools.
- `order-pcbway` is marked so that Claude never loads it by itself. Ask Claude to order your
  boards and it tells you to run the skill yourself. That is on purpose: an order costs
  money.

## Agents

An agent is a helper Claude hands one job to, with its own tools and a fresh context. The
skills dispatch them when they need to, in parallel for several jobs, and you can ask for one
by name.

| Agent | Does | Tools |
|---|---|---|
| `parts-researcher` | Looks up one part or one question about parts: datasheet limits, pinout, package, stock and price, equivalents. Every number comes back with the URL it was read from and where on the page. It says "Not found" instead of guessing, and reports a disagreement between sources instead of averaging it. | `WebSearch`, `WebFetch`, `Read`: it cannot edit anything. |
| `check-writer` | Writes one design check for a hazard you name, under `checks/`, and shows it failing on a planted mistake in a scratch copy of the project, outside the project folder. It returns the check's path, the `MUTANTS` entry for you to add to `mutants.py`, the check's result on your real board and the failing output. If the check fails on the real board, that is the finding: it does not adjust the check. | `Read`, `Grep`, `Glob`, `Edit`, `Write`, `Bash`. It follows the `add-check` skill. |

`new-board` uses `parts-researcher` for every part that needs a datasheet figure, and
`add-check` uses it to find a limit's source and `check-writer` to write one check per
hazard.

## The guard hooks

Two hooks run before every `Edit` and `Write` tool call, whether Claude or one of its agents
makes it. If a hook refuses, the call does not happen and Claude is shown the reason.

| Hook | Applies to | Refuses |
|---|---|---|
| `guard_generated.py` | Every Edit and Write, from anyone. | A file in `kicad/`, `out/`, `golden/` or `fab/` of a board project. |
| `confine_check_writer.py` | Edit and Write by the `check-writer` agent only. | A file in the session's board project that is not under its `checks/` folder. |

The first stops edits to generated files, which the next build overwrites. A board project
is any folder that holds a `pcbkit.toml`; the hook looks at the first folder below it,
ignoring capitals (the default macOS file system does not tell `Kicad/` from `kicad/`) and
following links. It tells Claude:

> Blocked: .../kicad/blinky.kicad_pcb is in kicad/, which pcbkit generates, so an edit there
> is lost on the next build. Change the project's Python or pcbkit.toml instead (design.py
> for parts and nets, layout.py for positions and the outline, routing.py for routing,
> silk.py for labels), then rerun pcbkit build, route and finalize as needed.

The second confines the check-writer to `checks/`, so the agent that writes checks cannot
edit your design or your `specs.py` to make its own check pass. It tells the agent to keep a
limit and its source at the top of the check module, to work in a scratch copy outside the
project folder, and to hand back anything else (a `MUTANTS` entry) to the caller. A scratch
copy elsewhere is a different project, so the agent may edit it freely; that is where it
proves the check fails. Other agents, the main session and files outside any project are
not affected.

A hook that cannot read its event exits with status 1. Claude Code treats that as a
non-blocking error: the call goes ahead and the error is reported to you. A broken guard is
noticed, but it does not block.

### What the hooks cannot see

They watch the `Edit` and `Write` tools and nothing else. A change made through `Bash` (a
heredoc, `sed -i`, `cp`, `git checkout`) never reaches them, nor does a change made by any
other tool. So the guard is a rail, not a sandbox:

- The `board-workflow` skill tells Claude never to edit those folders by hand, and not to
  reach them with a shell either. The `check-writer` has `Bash` so that it can run its check
  in the scratch copy, and it is told not to use it to get round the hooks.
- `kicad/` and `out/` are rewritten by the next build, route or finalize. `golden/` is
  rewritten only by `promote`, and `finalize` builds from it, so a stray edit there matters
  most. The `.gitignore` that `pcbkit new` writes ignores `kicad/` and `out/` but not
  `golden/`: if you keep the project in git, `git diff golden/` shows a hand edit.

## Working on the plugin

`claude plugin validate ~/src/pcbkit` checks the manifest; pointed at `skills/` or `agents/`
it checks those. The `evals/` folder holds four cases for `claude plugin eval`: "route my board"
loads `board-workflow`, "add a check that the LED current is under 20 mA" loads `add-check`,
"order these boards" does not load `order-pcbway` and points to it, and an Edit of a file in
`kicad/` is refused with the reason above. The hooks and the skills' wording are also covered
by `tests/unit/test_plugin.py`, `test_guard_generated.py` and `test_confine_check_writer.py`.
