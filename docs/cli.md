# CLI reference

Every command and option of `pcbkit`, in the order you use them. The sections below are
generated from the command line itself when the site is built, so they say what the
installed version does.

Every command takes `-h` or `--help`, and `pcbkit --version` prints the version.

Run the commands from inside a board project: a folder that holds a `pcbkit.toml`, or
any folder below one. The ones that need KiCad's `pcbnew` module (`build`, `route`,
`promote`, `finalize`, `check`, `mutants` and `compare`) run from the project's own
`.venv/bin/pcbkit`, which `pcbkit setup` makes; the others run from any `pcbkit`,
including one started with `uvx`. [Concepts](concepts.md#the-two-tiers) explains why.

A command that cannot do its job prints what is wrong and what to do about it, and
exits 1. Three commands use other codes where they have more to say: `check` exits with
pytest's code (0 all passed, 1 a check failed, 5 nothing matched), `mutants` exits 2
when its control run does not pass, and `compare` exits 2 when a file is not a board.

::: mkdocs-click
    :module: pcbkit.cli
    :command: new_cmd
    :prog_name: pcbkit new
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: doctor_cmd
    :prog_name: pcbkit doctor
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: setup_cmd
    :prog_name: pcbkit setup
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: sch_cmd
    :prog_name: pcbkit sch
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: build_cmd
    :prog_name: pcbkit build
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: route_cmd
    :prog_name: pcbkit route
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: promote_cmd
    :prog_name: pcbkit promote
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: finalize_cmd
    :prog_name: pcbkit finalize
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: check_cmd
    :prog_name: pcbkit check
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: mutants_cmd
    :prog_name: pcbkit mutants
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: report_cmd
    :prog_name: pcbkit report
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: quote_cmd
    :prog_name: pcbkit quote
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: compare_cmd
    :prog_name: pcbkit compare
    :depth: 1

::: mkdocs-click
    :module: pcbkit.cli
    :command: shots_cmd
    :prog_name: pcbkit shots
    :depth: 1
