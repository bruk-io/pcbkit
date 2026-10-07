"""Helpers for the tests of `pcbkit new`: write a tree of files, list one, and more."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "blinky"


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Write text files (paths relative to ``root``), and return ``root``."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def materialise(files: dict[str, bytes], root: Path) -> Path:
    """Write ``files`` (as ``scaffold.template_files`` returns them) under ``root``."""
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def listing(root: Path) -> list[str]:
    """Return every file under ``root``, as sorted ``/`` paths."""
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def make_checkout(root: Path) -> Path:
    """Make a folder that looks like a pcbkit checkout, and return it."""
    (root / "pcbkit").mkdir(parents=True)
    (root / "pcbkit" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pcbkit"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root
