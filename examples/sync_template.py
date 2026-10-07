"""Make each template that `pcbkit new` copies a copy of its example board.

`pcbkit new --from blinky` copies pcbkit/templates/board, which is examples/blinky
without what commands generate and without the two files `new` writes for each board.
Run this from a pcbkit checkout after changing an example:

    uv run python examples/sync_template.py            # update the templates
    uv run python examples/sync_template.py --check    # only say if they are up to date

A unit test (tests/unit/test_scaffold.py) runs the check, so a change to one side that
the other does not have fails the test run.
"""

from __future__ import annotations

from pathlib import Path

import click

from pcbkit import scaffold

EXAMPLES = Path(__file__).resolve().parent


def _shown(path: Path) -> Path:
    """Return ``path`` relative to the repository when it is inside it."""
    try:
        return path.relative_to(EXAMPLES.parent)
    except ValueError:
        return path


@click.command()
@click.option("--check", is_flag=True, help="Report differences and change nothing.")
def main(check: bool) -> None:
    """Update the templates from the examples, or with --check say if they differ."""
    differing = False
    for name, folder in scaffold.TEMPLATES.items():
        example = EXAMPLES / name
        template = scaffold.TEMPLATE_ROOT / folder
        if check:
            lines = scaffold.template_differences(example, template)
        else:
            lines = scaffold.sync_template(example, template)
        shown = _shown(template)
        for line in lines:
            click.echo(f"{shown}: {line}")
        if not lines:
            click.echo(f"{shown}: up to date with examples/{name}")
        differing = differing or bool(lines)
    if check and differing:
        raise click.ClickException(
            "the templates differ from their examples: "
            "run `uv run python examples/sync_template.py`"
        )


if __name__ == "__main__":
    main()
