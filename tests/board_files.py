"""Helpers for tests that build a small board project in a temporary folder."""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

TOML = """\
[board]
stem = "my_board"
title = "My Board"
rev = "A"
fab_name = "My_Board_revA"
"""


def write_file(path: Path, text: str) -> Path:
    """Write dedented ``text`` to ``path``, making its folder, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def write_project(root: Path, design: str, toml: str = TOML) -> Path:
    """Write a pcbkit.toml and a design.py into ``root`` and return the design path."""
    write_file(root / "pcbkit.toml", toml)
    return write_file(root / "design.py", design)


@contextmanager
def restored_imports() -> Iterator[None]:
    """Undo whatever the body does to sys.path and sys.modules."""
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)
