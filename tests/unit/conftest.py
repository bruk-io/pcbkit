"""Fixtures that apply to every unit test."""

from __future__ import annotations

import sys
import types

import pytest

from tests.fake_machine import FakeMachine
from tests.fake_pcbnew import make_pcbnew


@pytest.fixture(autouse=True)
def isolated_machine(machine: FakeMachine) -> FakeMachine:
    """Keep every unit test off the real machine, whether or not it asks for one."""
    return machine


@pytest.fixture
def fake_pcbnew(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake pcbnew (KiCad 10 flavour) for the length of one test.

    pcbkit.kicad.board imports pcbnew on first use, so putting the fake in sys.modules
    is all it takes, and it works whether or not KiCad's Python is the one running.
    """
    fake = make_pcbnew(10)
    monkeypatch.setitem(sys.modules, "pcbnew", fake)
    return fake
