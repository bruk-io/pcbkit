"""A board project's own symbol and footprint libraries.

Most parts come from KiCad's stock libraries. A part KiCad does not have (a dev board,
an edge connector, a package that later KiCad releases dropped) goes in the project's
own library, which pcbkit builds into ``kicad/`` from two places a project can use
together:

- ``footprints.py``, whose generator functions pcbkit calls (the hooks, below);
- the ``footprints/`` folder, whose ``.kicad_mod`` files are copied unchanged.

``footprints.py`` is optional, and so is every name in it:

``LIB``
    The library nickname: the part before the colon in ``mylib:PartName``. Defaults to
    the board's stem.
``symbols() -> list``
    The symbols, as S-expression nodes (``pcbkit.kicad.sexp``): each one
    ``["symbol", q("Name"), ...]``. Written to ``kicad/<LIB>.kicad_sym``.
``footprints() -> dict``
    Footprint name to its S-expression node, ``["footprint", q("Name"), ...]``. Each is
    written to ``kicad/<LIB>.pretty/<Name>.kicad_mod``.

Alongside the libraries pcbkit writes what KiCad needs to find them. kicad-cli reads a
project's ``sym-lib-table`` and ``fp-lib-table`` only when the project file exists, so
a minimal ``<stem>.kicad_pro`` is written too, unless there is one already: later
stages save the design rules into it, and it is never overwritten.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pcbkit import project
from pcbkit.kicad.sexp import dump, q
from pcbkit.project import Project, ProjectError

Node = list  # an S-expression list, as in pcbkit.kicad.sexp

# The header written on a symbol library. The format version is the one the other
# generated KiCad files use; KiCad upgrades older files when it saves them.
SYMBOL_LIB_VERSION = 20241209
GENERATOR = "pcbkit"
GENERATOR_VERSION = "9.0"

# A seed project file for kicad-cli. KiCad fills in the rest, keeping what is there, the
# first time it saves (pcbnew.SaveBoard does), so what it needs is only this much.
PROJECT_FILE_VERSION = 3


@dataclass(frozen=True)
class ProjectLibs:
    """Where a project's own libraries were written, and what is in them.

    ``symbol_file`` and ``footprint_dir`` are None when the project has no symbols or no
    footprints of its own.
    """

    nickname: str
    symbol_file: Path | None = None
    footprint_dir: Path | None = None
    symbols: tuple[str, ...] = ()
    footprints: tuple[str, ...] = ()

    @property
    def symbol_libs(self) -> dict[str, Path]:
        """Return ``{nickname: file}`` for the symbol library, or {} without one."""
        return {self.nickname: self.symbol_file} if self.symbol_file else {}


# --- the hooks --------------------------------------------------------------------


def library_name(hooks: ModuleType | None, stem: str) -> str:
    """Return ``LIB`` from footprints.py, or the board's stem when it has none."""
    name = getattr(hooks, "LIB", stem)
    if not isinstance(name, str) or not project.NAME_PATTERN.fullmatch(name):
        raise ProjectError(
            f"footprints.py: LIB should be a library name (letters, digits, '_', '-' "
            f"and '.', not starting with '.' or '-'), got {name!r}"
        )
    return name


def _node_name(node: object) -> str | None:
    """Return the name in an S-expression node such as ``["symbol", "R", ...]``."""
    if isinstance(node, list) and len(node) > 1 and isinstance(node[1], str):
        return str(node[1])
    return None


def symbol_nodes(hooks: ModuleType | None) -> list[Node]:
    """Return what footprints.py's ``symbols()`` makes, checked; [] if it has none."""
    hook = getattr(hooks, "symbols", None)
    if hook is None:
        return []
    nodes = hook()
    if not isinstance(nodes, (list, tuple)):
        raise ProjectError("footprints.py: symbols() should return a list of nodes")
    names: set[str] = set()
    for node in nodes:
        name = _node_name(node)
        if name is None or node[0] != "symbol":
            raise ProjectError(
                "footprints.py: symbols() should return nodes like "
                f'["symbol", q("Name"), ...], got {str(node)[:60]!r}'
            )
        if name in names:
            raise ProjectError(f"footprints.py: symbols() makes {name!r} twice")
        names.add(name)
    return list(nodes)


def footprint_nodes(hooks: ModuleType | None) -> dict[str, Node]:
    """Return what footprints.py's ``footprints()`` makes, checked; {} without one."""
    hook = getattr(hooks, "footprints", None)
    if hook is None:
        return {}
    made = hook()
    if not isinstance(made, Mapping):
        raise ProjectError("footprints.py: footprints() should return a dict of nodes")
    for name, node in made.items():
        if not isinstance(name, str) or not project.NAME_PATTERN.fullmatch(name):
            raise ProjectError(
                f"footprints.py: {name!r} is not usable as a footprint file name "
                "(letters, digits, '_', '-' and '.')"
            )
        if _node_name(node) != name or node[0] != "footprint":
            raise ProjectError(
                f'footprints.py: footprints()[{name!r}] should be a ["footprint", '
                f"q({name!r}), ...] node whose name matches its key"
            )
    return dict(made)


# --- writing the libraries --------------------------------------------------------


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 with Unix line ends."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_symbol_lib(path: Path, symbols: Sequence[Node]) -> None:
    """Write ``symbols`` as a KiCad symbol library at ``path``."""
    lib = [
        "kicad_symbol_lib",
        ["version", SYMBOL_LIB_VERSION],
        ["generator", q(GENERATOR)],
        ["generator_version", q(GENERATOR_VERSION)],
        *symbols,
    ]
    _write(path, dump(lib) + "\n")


def write_footprint_lib(directory: Path, footprints: Mapping[str, Node]) -> list[Path]:
    """Write each footprint to ``<directory>/<name>.kicad_mod``; return the files."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, node in footprints.items():
        path = directory / f"{name}.kicad_mod"
        _write(path, dump(node) + "\n")
        written.append(path)
    return written


def vendored_files(root: Path) -> list[Path]:
    """Return the ``.kicad_mod`` files in the project's ``footprints/``, sorted."""
    return sorted((root / "footprints").glob("*.kicad_mod"))


def copy_vendored(files: Sequence[Path], directory: Path) -> list[Path]:
    """Copy vendored footprint files into ``directory`` unchanged; return the copies."""
    directory.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy(path, directory)) for path in files]


def _escape(text: str) -> str:
    """Return ``text`` safe inside a quoted S-expression string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def lib_table(kind: str, nickname: str, filename: str | None, descr: str) -> str:
    """Return the text of a ``sym-lib-table`` (kind "sym") or ``fp-lib-table`` ("fp").

    The table lists the project's own library, found next to the project file through
    KiCad's ``${KIPRJMOD}``; ``filename`` None gives an empty table.
    """
    lines = [f"({kind}_lib_table", "  (version 7)"]
    if filename is not None:
        lines.append(
            f'  (lib (name "{nickname}")(type "KiCad")'
            f'(uri "${{KIPRJMOD}}/{filename}")(options "")(descr "{_escape(descr)}"))'
        )
    lines.append(")")
    return "\n".join(lines) + "\n"


def ensure_project_file(directory: Path, stem: str) -> Path:
    """Write a minimal ``<stem>.kicad_pro`` unless one exists; return its path."""
    path = directory / f"{stem}.kicad_pro"
    if not path.exists():
        seed = {"meta": {"filename": path.name, "version": PROJECT_FILE_VERSION}}
        _write(path, json.dumps(seed, indent=2) + "\n")
    return path


def write_lib_tables(
    out: Path, nickname: str, title: str, symbol_file: Path | None, pretty: Path | None
) -> None:
    """Write ``sym-lib-table`` and ``fp-lib-table`` into ``out``.

    Each lists the project's own library when there is one, and is empty otherwise, so
    a library the project dropped does not stay listed from an earlier build.
    """
    _write(
        out / "sym-lib-table",
        lib_table(
            "sym",
            nickname,
            symbol_file.name if symbol_file else None,
            f"{title}: project symbols",
        ),
    )
    _write(
        out / "fp-lib-table",
        lib_table(
            "fp",
            nickname,
            pretty.name if pretty else None,
            f"{title}: project footprints",
        ),
    )


def write_project_libs(proj: Project) -> ProjectLibs:
    """Build the project's own libraries and library tables into its kicad/ folder.

    Calls the hooks in footprints.py (if there is one), copies the files in
    ``footprints/`` and writes ``sym-lib-table``, ``fp-lib-table`` and, if missing,
    the project file. A project with no libraries of its own gets empty tables.
    """
    board = proj.config.board
    out = proj.kicad_dir
    hooks = project.import_optional_project_module(proj.root, "footprints")
    nickname = library_name(hooks, board.stem)
    symbols = symbol_nodes(hooks)
    generated = footprint_nodes(hooks)
    vendored = vendored_files(proj.root)
    clash = sorted(set(generated) & {path.stem for path in vendored})
    if clash:
        raise ProjectError(
            f"footprints.py makes {', '.join(clash)}, and footprints/ holds a file of "
            "the same name: keep one"
        )
    out.mkdir(parents=True, exist_ok=True)

    symbol_file = None
    if symbols:
        symbol_file = out / f"{nickname}.kicad_sym"
        write_symbol_lib(symbol_file, symbols)
    footprint_dir = None
    if generated or vendored:
        footprint_dir = out / f"{nickname}.pretty"
        write_footprint_lib(footprint_dir, generated)
        copy_vendored(vendored, footprint_dir)
    write_lib_tables(out, nickname, board.title, symbol_file, footprint_dir)
    ensure_project_file(out, board.stem)
    return ProjectLibs(
        nickname=nickname,
        symbol_file=symbol_file,
        footprint_dir=footprint_dir,
        symbols=tuple(str(_node_name(node)) for node in symbols),
        footprints=tuple(sorted(set(generated) | {path.stem for path in vendored})),
    )
