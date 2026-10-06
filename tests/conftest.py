"""Shared fixtures for every test tier."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbkit.kicad import env
from tests.fake_machine import FakeMachine


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeMachine:
    """Replace the real machine with an empty fake for the length of one test.

    Nothing is installed, PATH is empty, $HOME is a temporary folder, the OS is Linux
    and the override variables are unset. A test adds what it needs to the returned
    FakeMachine (or to ``os_name``), and any command it did not set up fails loudly.
    Unit tests get this automatically (tests/unit/conftest.py); a test that wants the
    real machine, such as one marked ``kicad``, simply does not ask for it.
    """
    fake = FakeMachine(root=tmp_path / "machine")
    fake.home.mkdir(parents=True)
    for name in ("KICAD_SHARE", "FREEROUTING_JAR", "JAVA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(fake.home))
    monkeypatch.setattr(env, "MAC_APP", fake.mac_app)
    monkeypatch.setattr(env, "LINUX_SHARE", fake.linux_share)
    monkeypatch.setattr(env, "USR_BIN", fake.usr_bin)
    monkeypatch.setattr(env, "HOMEBREW_PREFIXES", (fake.brew_arm, fake.brew_intel))
    monkeypatch.setattr(env, "host_os", lambda: fake.os_name)
    monkeypatch.setattr(env, "_which", lambda name: fake.path_tools.get(name))
    monkeypatch.setattr(env, "_run", fake.run)
    return fake
