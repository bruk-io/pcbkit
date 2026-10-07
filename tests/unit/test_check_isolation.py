"""Unit tests that pcbkit's own test run loads no check plugin and writes no results.

``pcbkit.check.plugin`` turns a pytest run into the checks of one board: it needs a
board project, and it writes ``out/checks/results.json`` when the run ends. So it must
be loaded by name only, on the command line ``pcbkit check`` builds
(``-p pcbkit.check.plugin``), and never by an entry point (pytest would load it into
every run in the environment, this one included) or by a conftest.

These tests look at the run they are part of, and at the files that decide what is
loaded. Where a test can only pass because something is absent, a companion test shows
the scan would see it if it were there.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from pathlib import Path
from typing import Any

import pytest

import pcbkit.check
from pcbkit.check import plugin as loaded_plugin
from pcbkit.check import results

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "pcbkit.check.plugin"
# Fixtures the plugin provides; none may be visible to a unit test of pcbkit itself.
PLUGIN_FIXTURES = ["specs", "circuits", "nl", "record", "out_dir"]


def entry_points_of(group: str) -> list[importlib.metadata.EntryPoint]:
    """Return the installed entry points of ``group`` on Python 3.9 and later."""
    found = importlib.metadata.entry_points()
    if hasattr(found, "select"):  # 3.10 and later
        return list(found.select(group=group))
    return list(found.get(group, ()))  # 3.9: a dict of group to entry points


def comes_from_pcbkit(entry: importlib.metadata.EntryPoint) -> bool:
    """Return True if the entry point is named for pcbkit or points into it."""
    module = entry.value.partition(":")[0].strip()
    return module.split(".")[0] == "pcbkit" or entry.name.lower().startswith("pcbkit")


def keys_named(node: Any, name: str, path: str = "") -> list[str]:
    """Return the dotted path of every table or key called ``name`` inside ``node``."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key == name:
                found.append(here)
            found += keys_named(value, name, here)
    elif isinstance(node, list):
        for item in node:
            found += keys_named(item, name, path)
    return found


def pyproject() -> dict[str, Any]:
    """Return the parsed pyproject.toml of the repository."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


# --- (a) no entry point loads the plugin ------------------------------------------


def test_pcbkit_registers_no_pytest_entry_point() -> None:
    """Find no ``pytest11`` entry point that comes from pcbkit."""
    ours = [e for e in entry_points_of("pytest11") if comes_from_pcbkit(e)]
    assert ours == [], (
        f"pcbkit must not register a pytest plugin by entry point: {ours}. "
        "Load it by name with -p, as `pcbkit check` does."
    )


def test_the_entry_point_scan_sees_pcbkits_own_metadata() -> None:
    """Find pcbkit's console script, so that no pytest11 entry means something."""
    scripts = entry_points_of("console_scripts")
    assert any(e.name == "pcbkit" and e.value == "pcbkit.cli:cli" for e in scripts), (
        "pcbkit is not installed in this environment, so the scan above proved nothing"
    )


def test_the_entry_point_test_would_catch_a_pcbkit_plugin() -> None:
    """Recognise the entry points that would load the plugin, and let others be."""
    make = importlib.metadata.EntryPoint
    assert comes_from_pcbkit(make("pcbkit", PLUGIN, "pytest11"))
    assert comes_from_pcbkit(make("check", "pcbkit.check.plugin:hooks", "pytest11"))
    assert comes_from_pcbkit(make("pcbkit_checks", "other_pkg.plug", "pytest11"))
    assert not comes_from_pcbkit(make("cov", "pytest_cov.plugin", "pytest11"))


# --- (b) nothing registered it in this session ------------------------------------


def test_the_plugin_is_not_registered_in_this_session(
    request: pytest.FixtureRequest,
) -> None:
    """Find the plugin neither by name, by object, nor by what it would have done."""
    manager = request.config.pluginmanager
    assert importlib.import_module(PLUGIN) is loaded_plugin  # imported, not registered
    assert not manager.has_plugin(PLUGIN)
    assert manager.get_plugin(PLUGIN) is None
    assert not manager.is_registered(loaded_plugin)
    assert manager.get_name(loaded_plugin) is None
    assert all(registered is not loaded_plugin for registered in manager.get_plugins())
    # Its pytest_configure would have put the project in the stash, and its
    # pytest_addoption would have added the --pcbkit-project option.
    assert request.config.stash.get(loaded_plugin.STATE, None) is None
    assert request.config.getoption("pcbkit_project", "no such option") == (
        "no such option"
    )


@pytest.mark.parametrize("name", PLUGIN_FIXTURES)
def test_the_plugins_fixtures_are_not_visible_to_a_unit_test(
    name: str, request: pytest.FixtureRequest
) -> None:
    """Fail to find ``specs``, ``nl`` and the rest, which only the plugin provides."""
    with pytest.raises(pytest.FixtureLookupError):
        request.getfixturevalue(name)


# --- (c) importing it writes nothing ----------------------------------------------


def test_importing_the_plugin_writes_no_results_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the plugin's module code afresh and find no results.json anywhere."""
    # A new import runs the module's top level again. The module other tests hold stays
    # in sys.modules and on the package, put back when this test ends.
    monkeypatch.delitem(sys.modules, PLUGIN)
    monkeypatch.setattr(pcbkit.check, "plugin", loaded_plugin)
    fresh = importlib.import_module(PLUGIN)
    assert fresh is not loaded_plugin  # it really did run again
    for base in {REPO_ROOT, Path.cwd()}:
        written = base / "out" / results.RESULTS_DIR / results.RESULTS_FILE
        assert not written.exists(), f"{written} exists: importing the plugin wrote it"


# --- (d) the project files do not load it -----------------------------------------


def test_pyproject_has_no_pytest11_entry_point_table() -> None:
    """Declare no ``[project.entry-points.pytest11]``, or any pytest11 key at all."""
    data = pyproject()
    assert "pytest11" not in data["project"].get("entry-points", {})
    assert keys_named(data, "pytest11") == []


def test_the_pyproject_scan_would_catch_a_pytest11_table() -> None:
    """Find a pytest11 table wherever it is written, so the check above can fail."""
    planted = tomllib.loads(
        "[project.entry-points.pytest11]\npcbkit = 'pcbkit.check.plugin'\n"
    )
    assert keys_named(planted, "pytest11") == ["project.entry-points.pytest11"]
    assert "project.scripts" in keys_named(pyproject(), "scripts")  # it sees real keys


def test_no_pytest_option_or_conftest_names_the_plugin() -> None:
    """Keep ``-p pcbkit.check.plugin`` and ``pytest_plugins`` out of this run."""
    addopts = pyproject().get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert PLUGIN not in str(addopts.get("addopts", ""))
    conftests = sorted((REPO_ROOT / "tests").rglob("conftest.py"))
    assert conftests, "no conftest.py found: the scan covered nothing"
    named = [str(p) for p in conftests if PLUGIN in p.read_text(encoding="utf-8")]
    assert named == []
