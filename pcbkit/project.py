"""Find a board project, load and validate its pcbkit.toml, import its modules.

A board project is a directory with a ``pcbkit.toml`` at its root (see
docs/project-interface.md). ``load_project`` finds the nearest one at or above a
directory and returns a ``Project``: the root plus a validated, frozen ``Config``.
Everything a user can get wrong (no file, bad TOML, a missing, misspelt or mistyped
key) raises a ``ProjectError``, which is a ``click.ClickException``, so the CLI prints
a message instead of a traceback.

``SCHEMA`` is the one table that says which keys exist, what type each has and what it
defaults to. The dataclasses below only hold the validated result, and
docs/project-interface.md is checked against ``SCHEMA`` by a unit test.
"""

from __future__ import annotations

import difflib
import importlib.util
import keyword
import math
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import click

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.9 and 3.10 have no tomllib; tomli is the same parser
    import tomli as tomllib

CONFIG_NAME = "pcbkit.toml"

# Directories of a board project that pcbkit regenerates; nobody edits them by hand.
GENERATED_DIRS = ("kicad", "out", "golden", "fab")

# Check groups a project can switch on. pcbkit.check owns the checks themselves; this
# is only the list of names a pcbkit.toml may use.
CHECK_GROUPS = ("kicad", "outputs", "fab", "copper", "circuit", "esp32s3")

# Fab profiles pcbkit has code for.
FAB_PROFILES = ("pcbway",)

# A stem or fab name ends up in file names: no spaces, slashes or leading dots.
NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class ProjectError(click.ClickException):
    """Report a problem with the board project or its pcbkit.toml."""


@dataclass(frozen=True)
class BoardConfig:
    """The ``[board]`` table: the names that end up in file names and title blocks."""

    stem: str
    title: str
    rev: str
    fab_name: str


@dataclass(frozen=True)
class StackupConfig:
    """The ``[stackup]`` table: board build-up in millimetres."""

    layers: int
    thickness_mm: float
    copper_mm: float


@dataclass(frozen=True)
class RouteConfig:
    """The ``[route]`` table: Freerouting settings and the eco reach."""

    freerouting_passes: int
    tries: int
    stall_timeout_s: int
    eco_unlock_reach_mm: float


@dataclass(frozen=True)
class StitchConfig:
    """The ``[stitch]`` table; ``dense`` boxes are (x0, y0, x1, y1, pitch) in mm."""

    pitch_mm: float
    dense: tuple[tuple[float, float, float, float, float], ...]
    gap_limit_mm: float


@dataclass(frozen=True)
class FabConfig:
    """The ``[fab]`` table: which fab house's rules and forms to use."""

    profile: str


@dataclass(frozen=True)
class ChecksConfig:
    """The ``[checks]`` table: the built-in check groups to run."""

    groups: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    """A validated pcbkit.toml, with every default filled in."""

    board: BoardConfig
    stackup: StackupConfig
    route: RouteConfig
    stitch: StitchConfig
    fab: FabConfig
    checks: ChecksConfig


@dataclass(frozen=True)
class Project:
    """A board project on disk: its root directory and its validated config."""

    root: Path
    config: Config

    @property
    def kicad_dir(self) -> Path:
        """Return the directory of generated KiCad files."""
        return self.root / "kicad"

    @property
    def out_dir(self) -> Path:
        """Return the directory of generated outputs (reports, renders, drawings)."""
        return self.root / "out"

    @property
    def golden_dir(self) -> Path:
        """Return the directory that holds the promoted, DRC-clean route."""
        return self.root / "golden"

    @property
    def fab_dir(self) -> Path:
        """Return the directory of files to send to the fab house."""
        return self.root / "fab"

    @property
    def archive_dir(self) -> Path:
        """Return the directory where the user keeps earlier revisions."""
        return self.root / "archive"


# --- schema -----------------------------------------------------------------------

REQUIRED = object()  # marks a key with no default


@dataclass(frozen=True)
class Rule:
    """How one key of pcbkit.toml is validated, and what it defaults to.

    ``kind`` picks the converter: ``text`` (non-empty string), ``name`` (a string that
    is safe in a file name), ``whole`` (integer >= 1), ``number`` (finite float > 0),
    ``choice`` (one of ``choices``), ``groups`` (list of names from ``choices``) or
    ``boxes`` (list of [x0, y0, x1, y1, pitch]). ``note`` is added to the error.
    """

    kind: str
    default: object = REQUIRED
    choices: tuple[object, ...] = ()
    note: str = ""


SCHEMA: dict[str, dict[str, Rule]] = {
    "board": {
        "stem": Rule("name"),
        "title": Rule("text"),
        "rev": Rule("text"),
        "fab_name": Rule("name"),
    },
    "stackup": {
        "layers": Rule(
            "whole", 2, choices=(2,), note="pcbkit builds two-layer boards only"
        ),
        "thickness_mm": Rule("number", 1.6),
        "copper_mm": Rule("number", 0.035),
    },
    "route": {
        "freerouting_passes": Rule("whole", 40),
        "tries": Rule("whole", 3),
        "stall_timeout_s": Rule("whole", 90),
        "eco_unlock_reach_mm": Rule("number", 8.0),
    },
    "stitch": {
        "pitch_mm": Rule("number", 5.0),
        "dense": Rule("boxes", ()),
        "gap_limit_mm": Rule("number", 3.4),
    },
    "fab": {
        "profile": Rule("choice", "pcbway", choices=FAB_PROFILES),
    },
    "checks": {
        "groups": Rule("groups", ("kicad", "outputs"), choices=CHECK_GROUPS),
    },
}


def _describe(value: object) -> str:
    """Return a value and its TOML type, for error messages."""
    names = {
        bool: "boolean",
        int: "integer",
        float: "float",
        str: "string",
        list: "array",
        dict: "table",
    }
    return f"{value!r} ({names.get(type(value), type(value).__name__)})"


def _finite(value: object) -> float | None:
    """Return ``value`` as a float if it is a finite int or float, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _to_text(rule: Rule, value: object) -> object:
    """Accept a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        hint = " (quote it to make it a string)" if _finite(value) is not None else ""
        raise ValueError(f"expected a non-empty string, got {_describe(value)}{hint}")
    return value


def _to_name(rule: Rule, value: object) -> object:
    """Accept a non-empty string that is safe to use in a file name."""
    text = _to_text(rule, value)
    if not NAME_PATTERN.fullmatch(str(text)):
        raise ValueError(
            f"{text!r} is used in file names: use letters, digits, '_', '-' and '.', "
            "with no spaces, and start with a letter, digit or '_'"
        )
    return text


def _to_whole(rule: Rule, value: object) -> object:
    """Accept an integer of 1 or more, within ``rule.choices`` when it has any."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"expected a whole number >= 1, got {_describe(value)}")
    if rule.choices and value not in rule.choices:
        allowed = " or ".join(str(choice) for choice in rule.choices)
        note = f" ({rule.note})" if rule.note else ""
        raise ValueError(f"must be {allowed}, got {value}{note}")
    return value


def _to_number(rule: Rule, value: object) -> object:
    """Accept a finite number above zero and return it as a float."""
    number = _finite(value)
    if number is None or number <= 0:
        raise ValueError(f"expected a number > 0, got {_describe(value)}")
    return number


def _to_choice(rule: Rule, value: object) -> object:
    """Accept one of ``rule.choices``."""
    if value not in rule.choices or not isinstance(value, str):
        allowed = ", ".join(repr(choice) for choice in rule.choices)
        raise ValueError(f"expected one of {allowed}, got {_describe(value)}")
    return value


def _to_groups(rule: Rule, value: object) -> object:
    """Accept a list of distinct names from ``rule.choices``."""
    allowed = ", ".join(repr(choice) for choice in rule.choices)
    if not isinstance(value, list):
        raise ValueError(
            f"expected a list of names from {allowed}, got {_describe(value)}"
        )
    for index, item in enumerate(value):
        if item not in rule.choices or not isinstance(item, str):
            raise ValueError(
                f"item {index} should be one of {allowed}, got {_describe(item)}"
            )
        if value.index(item) != index:
            raise ValueError(f"{item!r} is listed twice")
    return tuple(value)


def _to_boxes(rule: Rule, value: object) -> object:
    """Accept a list of [x0, y0, x1, y1, pitch] boxes with x0 < x1 and y0 < y1."""
    if not isinstance(value, list):
        raise ValueError(f"expected a list of boxes, got {_describe(value)}")
    boxes = []
    for index, item in enumerate(value):
        shape = "[x0, y0, x1, y1, pitch]"
        if not isinstance(item, list) or len(item) != 5:
            raise ValueError(f"box {index} should be {shape}, got {_describe(item)}")
        numbers = [_finite(n) for n in item]
        if None in numbers:
            raise ValueError(f"box {index} should be five finite numbers, got {item!r}")
        x0, y0, x1, y1, pitch = numbers
        if not (x0 < x1 and y0 < y1 and pitch > 0):
            raise ValueError(
                f"box {index} needs x0 < x1, y0 < y1 and pitch > 0, got {item!r}"
            )
        boxes.append((x0, y0, x1, y1, pitch))
    return tuple(boxes)


_CONVERTERS = {
    "text": _to_text,
    "name": _to_name,
    "whole": _to_whole,
    "number": _to_number,
    "choice": _to_choice,
    "groups": _to_groups,
    "boxes": _to_boxes,
}


def _suggest(word: str, known: Iterable[str]) -> str:
    """Return " (did you mean 'x'?)" for the closest known name, or an empty string."""
    close = difflib.get_close_matches(word, list(known), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _section(name: str, table: object, errors: list[str]) -> dict[str, Any]:
    """Validate one table against SCHEMA; return its values with defaults filled in."""
    rules = SCHEMA[name]
    values: dict[str, Any] = {}
    if table is None:
        required = [key for key, rule in rules.items() if rule.default is REQUIRED]
        if required:
            errors.append(f"missing table [{name}] with {', '.join(required)}")
            return values
        table = {}
    if not isinstance(table, dict):
        errors.append(f"[{name}] should be a table, got {_describe(table)}")
        return values
    for key in table:
        if key not in rules:
            errors.append(f"[{name}] unknown key {key!r}{_suggest(key, rules)}")
    for key, rule in rules.items():
        if key not in table:
            if rule.default is REQUIRED:
                errors.append(f"[{name}] missing required key {key!r}")
            else:
                values[key] = rule.default
            continue
        try:
            values[key] = _CONVERTERS[rule.kind](rule, table[key])
        except ValueError as err:
            errors.append(f"[{name}] {key}: {err}")
    return values


def parse_config(data: Mapping[str, object], source: str = CONFIG_NAME) -> Config:
    """Validate parsed TOML ``data`` and return a Config; raise ProjectError if invalid.

    Every problem is collected and reported together. ``source`` names the file in the
    message.
    """
    errors: list[str] = []
    for key, value in data.items():
        if key not in SCHEMA:
            kind = "table" if isinstance(value, dict) else "key"
            tail = "" if isinstance(value, dict) else " (keys belong inside a table)"
            errors.append(f"unknown {kind} {key!r}{_suggest(key, SCHEMA)}{tail}")
    values = {name: _section(name, data.get(name), errors) for name in SCHEMA}
    if errors:
        count = f"{len(errors)} problem{'' if len(errors) == 1 else 's'}"
        lines = "".join(f"\n  {error}" for error in errors)
        raise ProjectError(f"{source}: {count}{lines}")
    return Config(
        board=BoardConfig(**values["board"]),
        stackup=StackupConfig(**values["stackup"]),
        route=RouteConfig(**values["route"]),
        stitch=StitchConfig(**values["stitch"]),
        fab=FabConfig(**values["fab"]),
        checks=ChecksConfig(**values["checks"]),
    )


def load_config(path: Path) -> Config:
    """Read and validate the pcbkit.toml at ``path``."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise ProjectError(f"{path} not found") from None
    except OSError as err:
        raise ProjectError(f"cannot read {path}: {err.strerror or err}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        raise ProjectError(f"{path} is not valid TOML: {err}") from None
    return parse_config(data, str(path))


def find_root(start: Path | None = None) -> Path:
    """Return the nearest directory at or above ``start`` that has a pcbkit.toml.

    ``start`` defaults to the current directory.
    """
    here = Path.cwd() if start is None else Path(start)
    here = here.resolve()
    if here.is_file():
        here = here.parent
    for folder in (here, *here.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder
    raise ProjectError(
        f"no {CONFIG_NAME} in {here} or any folder above it. "
        "Run `pcbkit new NAME` to start a board project, or cd into one."
    )


def load_project(start: Path | None = None) -> Project:
    """Find the board project at or above ``start`` and load its config."""
    root = find_root(start)
    return Project(root=root, config=load_config(root / CONFIG_NAME))


# --- project modules --------------------------------------------------------------


def _is_loaded_from(module: ModuleType, path: Path) -> bool:
    """Return True if ``module`` was imported from the file at ``path``."""
    file = getattr(module, "__file__", None)
    return file is not None and Path(file).resolve() == path


def import_optional_project_module(root: Path, name: str) -> ModuleType | None:
    """Import ``<root>/<name>.py`` by file path; return None if the file is absent.

    The project root goes on ``sys.path`` and the module is registered in
    ``sys.modules`` under its bare name, so project modules can import each other
    (``import layout``) and every importer sees one module object. A second call for
    the same file returns that module; a different file with the same name replaces it.
    Errors raised by the project's own code propagate untouched, so the user sees the
    traceback through their file.
    """
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"{name!r} is not a module name")
    path = (Path(root) / f"{name}.py").resolve()
    if not path.is_file():
        return None
    previous = sys.modules.get(name)
    if previous is not None and _is_loaded_from(previous, path):
        return previous
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


def import_project_module(root: Path, name: str) -> ModuleType:
    """Import ``<root>/<name>.py``; raise ProjectError if the file is missing."""
    module = import_optional_project_module(root, name)
    if module is None:
        raise ProjectError(
            f"{Path(root) / (name + '.py')} not found: a board project needs a "
            f"{name}.py (see docs/project-interface.md)"
        )
    return module
