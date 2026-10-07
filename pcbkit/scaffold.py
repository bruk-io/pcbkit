"""``pcbkit new``: start a board project from a template, and keep the template honest.

A template is a folder of a finished board project that ships inside the package
(``pcbkit/templates/<folder>``), and every name ``--from`` accepts is an example board
in the pcbkit repository (``examples/<name>``) that its folder is generated from. So
there is one board to maintain: the example is built, routed, checked and kept in step
by its own tests, and the template is a copy of it.

* ``template_files`` says which files of an example belong to its template: not what a
  command generates (``kicad/``, ``out/``, ``fab/``, ``.venv/``), and not the two files
  ``new`` writes afresh for each board (``pyproject.toml`` and ``README.md``).
* ``sync_template`` and ``template_differences`` make a template equal to its example,
  and tell when it is not. ``examples/sync_template.py`` runs the first from a checkout,
  and a unit test runs the second, so the two folders cannot drift apart unnoticed.
* ``create_project`` is the command: copy the template into a new folder, give the
  board its names, move the golden route onto the new file stem, and write the
  project's ``pyproject.toml`` and ``README.md``.

Everything here is plain files and text: no KiCad, no pcbnew, no network.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from pcbkit.project import ProjectError, load_config

# What `pcbkit new --from NAME` accepts: the example's name, and the folder under
# pcbkit/templates/ that holds its template.
TEMPLATES = {"blinky": "board"}
DEFAULT_TEMPLATE = "blinky"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"

# A new project depends on pcbkit through git until pcbkit is on PyPI; a local checkout
# can stand in with --pcbkit-source (or PCBKIT_SOURCE).
PCBKIT_GIT = "git+https://github.com/bruk-io/pcbkit"
PCBKIT_SOURCE_ENV = "PCBKIT_SOURCE"
# pytest runs the checks (`pcbkit check`), so every project needs it beside pcbkit.
PROJECT_DEPENDENCIES = ("pytest>=8",)

# --- which files make a template ---------------------------------------------------

# Folders at the top of a project that commands generate or the user keeps for
# themselves. golden/ is not among them: it is the example's route, kept and renamed.
GENERATED_DIRS = ("kicad", "out", "fab", "archive")
# Folders and files that are noise wherever they are.
NOISE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache", ".git")
NOISE_FILES = (".DS_Store",)
# Top-level files that are not copied: `new` writes these two for each board, and a
# lock file belongs to whoever made it.
WRITTEN_BY_NEW = ("pyproject.toml", "README.md")
NOT_COPIED = (*WRITTEN_BY_NEW, "uv.lock")


def _is_template_file(relative: Path) -> bool:
    """Return True if the file at this path inside an example goes in its template."""
    parts = relative.parts
    if parts[0].startswith(".venv") or parts[0] in GENERATED_DIRS:
        return False
    if len(parts) == 1 and parts[0] in NOT_COPIED:
        return False
    if any(part in NOISE_DIRS for part in parts[:-1]) or parts[-1] in NOISE_FILES:
        return False
    return not parts[-1].endswith(".pyc")


def template_files(root: Path) -> dict[str, bytes]:
    """Return the files of the board project at ``root`` that a template holds.

    The keys are paths inside the project, with ``/`` as the separator, in sorted order;
    the values are the files' bytes.
    """
    found: dict[str, bytes] = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and _is_template_file(relative):
            found[relative.as_posix()] = path.read_bytes()
    return found


def template_differences(example: Path, template: Path) -> list[str]:
    """Return how ``template`` differs from what ``example`` would make it; [] if not.

    One line for each file the template lacks, each it should not have, and each that
    differs in content.
    """
    want = template_files(example)
    have = template_files(template) if Path(template).is_dir() else {}
    lines = [f"missing from the template: {name}" for name in want if name not in have]
    lines += [f"not in the example: {name}" for name in have if name not in want]
    lines += [
        f"differs: {name}" for name in want if name in have and want[name] != have[name]
    ]
    return lines


def sync_template(example: Path, template: Path) -> list[str]:
    """Make ``template`` hold exactly the template files of ``example``.

    Return what changed, as ``template_differences`` words it. Only files that are
    not meant to be there are removed, so a wrong ``template`` cannot wipe a folder.
    """
    changes = template_differences(example, template)
    want = template_files(example)
    have = template_files(template) if Path(template).is_dir() else {}
    for name in have:
        if name not in want:
            (Path(template) / name).unlink()
    for name, data in want.items():
        target = Path(template) / name
        if have.get(name) != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    for folder in sorted(Path(template).rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return changes


# --- names ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Names:
    """What a board is called, derived from the NAME given to ``pcbkit new``.

    For ``my-board``: the folder and the distribution name ``my-board``, the KiCad file
    stem ``my_board``, the title ``My Board`` and the fab files' prefix
    ``My_Board_revA``.
    """

    project: str
    stem: str
    title: str
    fab_name: str


def board_names(name: str, rev: str = "A") -> Names:
    """Return the names of a board called ``name``; raise ProjectError if it cannot be.

    ``name`` is split into words at hyphens, underscores, dots and spaces. It may hold
    letters and digits only besides those, because the stem and the fab name end up in
    file names.
    """
    bad = sorted(set(re.sub(r"[A-Za-z0-9 _.-]", "", name)))
    if bad:
        raise ProjectError(
            f"{name!r} has {', '.join(repr(c) for c in bad)} in it: a board's name can "
            "use letters, digits, hyphens and underscores, such as my-board"
        )
    words = [word for word in re.split(r"[\s_.-]+", name) if word]
    if not words:
        raise ProjectError(
            f"{name!r} has no letters or digits to name a board with: "
            "give a name such as my-board"
        )
    shown = [word[:1].upper() + word[1:] for word in words]
    return Names(
        project="-".join(word.lower() for word in words),
        stem="_".join(word.lower() for word in words),
        title=" ".join(shown),
        fab_name="_".join(shown) + f"_rev{rev}",
    )


def rename_board(text: str, values: Mapping[str, str]) -> str:
    """Return a pcbkit.toml's text with the given ``[board]`` keys set to new values.

    Only the value changes: the key, the spacing and any comment after it stay. Raise
    ProjectError for a key that the ``[board]`` table does not have on a line of its
    own, which means the template is not what this function was written for.
    """
    lines = text.splitlines(keepends=True)
    in_board = False
    done: set[str] = set()
    for index, line in enumerate(lines):
        header = re.match(r"\s*\[([^\]]*)\]", line)
        if header:
            in_board = header.group(1).strip() == "board"
            continue
        found = re.match(r'(\s*(\w+)\s*=\s*)"[^"\r\n]*"', line) if in_board else None
        if found and found.group(2) in values:
            key = found.group(2)
            lines[index] = (
                found.group(1) + json.dumps(values[key]) + line[found.end() :]
            )
            done.add(key)
    missing = sorted(set(values) - done)
    if missing:
        raise ProjectError(
            "the template's pcbkit.toml has no quoted "
            + ", ".join(missing)
            + " in its [board] table to rename"
        )
    return "".join(lines)


def retarget_golden(folder: Path, old: str, new: str) -> list[str]:
    """Move a copied golden/ folder from file stem ``old`` to ``new``; return its files.

    ``finalize`` looks for ``<stem>.ses`` and ``<stem>.dsn`` there, so the files are
    renamed, and the stem is also written inside them (the session's design name, the
    DSN's first line, the schematic file each footprint names). Whole words only.
    """
    if not Path(folder).is_dir():
        return []
    files = sorted(path for path in Path(folder).iterdir() if path.is_file())
    if old == new:
        return [path.name for path in files]
    word = re.compile(rf"(?<![\w.-]){re.escape(old)}(?![\w-])")
    made = []
    for path in files:
        text = word.sub(new, path.read_text(encoding="utf-8"))
        target = path.with_name(word.sub(new, path.name))
        path.unlink()
        target.write_text(text, encoding="utf-8")
        made.append(target.name)
    return made


# --- the files `new` writes -----------------------------------------------------------


def pyproject_text(names: Names, source: Path | None = None) -> str:
    """Return the project's pyproject.toml.

    It depends on pcbkit from git, or from the checkout at ``source`` (written as an
    editable path source, which uv wants beside a plain ``pcbkit`` dependency). The
    project is not a package: ``[tool.uv] package = false`` makes ``uv sync`` install
    its dependencies and nothing else.
    """
    if source is None:
        pcbkit = f"pcbkit @ {PCBKIT_GIT}"
        sources = ""
    else:
        pcbkit = "pcbkit"
        sources = (
            "\n# pcbkit comes from a local checkout (pcbkit new --pcbkit-source).\n"
            "[tool.uv.sources]\n"
            f"pcbkit = {{ path = {json.dumps(str(source))}, editable = true }}\n"
        )
    description = f"{names.title}: a PCB project made with pcbkit"
    dependencies = "".join(
        f"    {json.dumps(dep)},\n" for dep in (pcbkit, *PROJECT_DEPENDENCIES)
    )
    return (
        "[project]\n"
        f"name = {json.dumps(names.project)}\n"
        'version = "0.1.0"\n'
        f"description = {json.dumps(description)}\n"
        "# Not higher: KiCad's Python, which pcbkit setup builds .venv on, is 3.9.\n"
        'requires-python = ">=3.9"\n'
        "# pytest runs the checks (pcbkit check).\n"
        f"dependencies = [\n{dependencies}]\n"
        "\n"
        "[tool.uv]\n"
        "package = false\n" + sources
    )


README = """\
# {title}

A board project for pcbkit, started with `pcbkit new {name}` from the `{template}`
example: a 2-pin power connector, a resistor and an LED on a two-layer board,
30 x 20 mm. Everything in this folder is yours to change.

## Commands

Once, to make `.venv` on KiCad's Python and fetch Freerouting:

```
pcbkit setup
```

Then, from this folder:

```
.venv/bin/pcbkit build          # schematic, ERC, netlist and placement
.venv/bin/pcbkit route          # Freerouting, the pours and DRC
.venv/bin/pcbkit promote        # keep the route that passed as golden/
.venv/bin/pcbkit finalize       # the board from golden/, DRC, Gerbers, BOM
.venv/bin/pcbkit check          # the design checks
.venv/bin/pcbkit mutants        # plant each mistake in mutants.py: all must be caught
```

`golden/` holds the {template} example's route, so `finalize` works straight after
`build`. After you change `design.py` or `layout.py`, run `route` and `promote` again,
and `finalize` builds your board.

## Files

| File | What it says |
|---|---|
| `pcbkit.toml` | Name and revision, stackup, router settings, check groups. |
| `design.py` | The circuit: the parts, and the net on each pin. |
| `layout.py` | The board's size, and where each part sits. |
| `routing.py` | What routing needs: net classes, design rules, the pours. |
| `silk.py` | The silkscreen text. |
| `specs.py` | The numbers the checks read, with their sources. |
| `checks/` | Your own checks. |
| `mutants.py` | Mistakes planted in `design.py` that the checks must catch. |
| `golden/`, `kicad/`, `out/`, `fab/` | Made by pcbkit: never edit by hand. |

The reference for every file is `docs/project-interface.md` in the pcbkit repository.
"""


def readme_text(names: Names, template: str) -> str:
    """Return the project's README.md."""
    return README.format(title=names.title, name=names.project, template=template)


# --- the command ----------------------------------------------------------------------


class ScaffoldError(click.ClickException):
    """Say why a board project could not be made."""


@dataclass(frozen=True)
class Created:
    """What ``create_project`` made: the folder, the names, and every file in it."""

    root: Path
    names: Names
    template: str
    source: str
    files: tuple[str, ...]


def pcbkit_checkout(path: Path) -> Path:
    """Return ``path``, resolved, if it is a pcbkit checkout; else raise an error."""
    where = Path(path).expanduser().resolve()
    project = where / "pyproject.toml"
    named = project.is_file() and re.search(
        r'(?m)^name\s*=\s*"pcbkit"\s*$', project.read_text(encoding="utf-8")
    )
    if not named or not (where / "pcbkit" / "__init__.py").is_file():
        raise ScaffoldError(
            f"{where} is not a pcbkit checkout: there is no pyproject.toml for pcbkit "
            "in it. Point --pcbkit-source at the folder you cloned pcbkit into."
        )
    return where


def _empty_folder(path: Path) -> bool:
    """Return True if ``path`` is a folder with nothing in it."""
    return path.is_dir() and not any(path.iterdir())


def _clear(folder: Path, remove_folder: bool) -> None:
    """Take out everything a failed ``create_project`` wrote into ``folder``.

    The folder was new or empty, so all of it was written here. ``remove_folder`` is
    True if it did not exist before.
    """
    if remove_folder:
        shutil.rmtree(folder, ignore_errors=True)
        return
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def create_project(
    name: str,
    template: str = DEFAULT_TEMPLATE,
    pcbkit_source: Path | None = None,
) -> Created:
    """Make the board project ``name`` from a template, in a new folder of that name.

    ``name`` may be a path: the board is named after its last part. The folder must not
    exist, or must be empty. ``pcbkit_source`` is a pcbkit checkout for the project to
    depend on instead of the git repository. Raise a ClickException, and leave nothing
    behind, if the project cannot be made.
    """
    if template not in TEMPLATES:
        known = ", ".join(sorted(TEMPLATES))
        raise ScaffoldError(f"no template called {template!r}: choose from {known}")
    folder = TEMPLATE_ROOT / TEMPLATES[template]
    if not (folder / "pcbkit.toml").is_file():
        raise ScaffoldError(
            f"pcbkit's {template} template is missing from {folder}: reinstall pcbkit"
        )
    source = pcbkit_checkout(pcbkit_source) if pcbkit_source is not None else None
    target = Path(name).expanduser().resolve()
    if target.exists() and not _empty_folder(target):
        raise ScaffoldError(
            f"{target} already exists and is not empty: choose another name, "
            "or move it out of the way"
        )
    board = load_config(folder / "pcbkit.toml").board
    names = board_names(target.name, board.rev)

    made_folder = not target.exists()
    try:
        target.mkdir(parents=True, exist_ok=True)
        for relative, data in template_files(folder).items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        toml = target / "pcbkit.toml"
        toml.write_text(
            rename_board(
                toml.read_text(encoding="utf-8"),
                {"stem": names.stem, "title": names.title, "fab_name": names.fab_name},
            ),
            encoding="utf-8",
        )
        retarget_golden(target / "golden", board.stem, names.stem)
        (target / "pyproject.toml").write_text(
            pyproject_text(names, source), encoding="utf-8"
        )
        (target / "README.md").write_text(
            readme_text(names, template), encoding="utf-8"
        )
        load_config(toml)  # what we wrote must be a pcbkit.toml pcbkit accepts
    except (OSError, click.ClickException) as err:
        _clear(target, remove_folder=made_folder)
        if isinstance(err, click.ClickException):
            raise
        raise ScaffoldError(
            f"could not write {target}: {err.strerror or err}"
        ) from None
    files = tuple(
        sorted(
            p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()
        )
    )
    return Created(
        root=target,
        names=names,
        template=template,
        source=str(source) if source is not None else PCBKIT_GIT,
        files=files,
    )


def format_result(created: Created, cwd: Path | None = None) -> str:
    """Return what `pcbkit new` prints: what was made, and what to type next."""
    here = Path.cwd() if cwd is None else Path(cwd)
    try:
        shown = created.root.relative_to(here)
    except ValueError:
        shown = created.root
    lines = [
        f"Made {created.names.title} in {shown}/, from the {created.template} example "
        f"({len(created.files)} files).",
        f"pcbkit comes from {created.source}.",
        "",
        "Next:",
        f"  cd {shown}",
        "  pcbkit setup",
        "  .venv/bin/pcbkit build",
        "",
        "README.md has the rest of the commands.",
    ]
    return "\n".join(lines)
