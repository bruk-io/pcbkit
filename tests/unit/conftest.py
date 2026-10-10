"""Fixtures that apply to every unit test."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pcbkit import datasheet_find, mouser
from tests.fake_machine import FakeMachine
from tests.fake_pcbnew import make_pcbnew


@pytest.fixture(autouse=True)
def isolated_machine(machine: FakeMachine) -> FakeMachine:
    """Keep every unit test off the real machine, whether or not it asks for one."""
    return machine


@pytest.fixture(autouse=True)
def no_mouser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every unit test off Mouser; a test that wants answers fakes them."""

    def refuse(method: str, url: str, body: bytes | None, timeout: float) -> Any:
        raise AssertionError(f"a unit test reached for Mouser: {method} {url[:40]}")

    monkeypatch.setattr(mouser, "_transport", refuse)
    monkeypatch.setattr(mouser, "_sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def no_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every unit test from downloading or reading a real PDF."""

    def refuse_download(url: str) -> bytes:
        raise AssertionError(f"a unit test reached for the web: {url}")

    def refuse_pdftotext(tool: str, pdf: Any) -> str:
        raise AssertionError(f"a unit test ran pdftotext on {pdf}")

    monkeypatch.setattr(datasheet_find, "_download", refuse_download)
    monkeypatch.setattr(datasheet_find, "_pdftotext", refuse_pdftotext)
    monkeypatch.setattr(datasheet_find, "_sleep", lambda seconds: None)


@pytest.fixture
def fake_pcbnew(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake pcbnew (KiCad 10 flavour) for the length of one test.

    pcbkit.kicad.board imports pcbnew on first use, so putting the fake in sys.modules
    is all it takes, and it works whether or not KiCad's Python is the one running.
    """
    fake = make_pcbnew(10)
    monkeypatch.setitem(sys.modules, "pcbnew", fake)
    return fake
