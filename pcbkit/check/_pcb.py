"""Reach pcbnew from the check engines without importing it until it is needed.

The engines import on a machine with no KiCad (their pure parts have unit tests that
run anywhere); the functions that read a board call ``pcbnew()`` when they run.
"""

from __future__ import annotations

from typing import Any


def pcbnew() -> Any:
    """Return the pcbnew module, imported on first use."""
    import pcbnew as module

    return module


def to_mm(value: Any) -> float:
    """Return a pcbnew length (internal units) in millimetres."""
    return pcbnew().ToMM(value)
