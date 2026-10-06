"""Fixtures that apply to every unit test."""

from __future__ import annotations

import pytest

from tests.fake_machine import FakeMachine


@pytest.fixture(autouse=True)
def isolated_machine(machine: FakeMachine) -> FakeMachine:
    """Keep every unit test off the real machine, whether or not it asks for one."""
    return machine
