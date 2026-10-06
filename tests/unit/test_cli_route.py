"""Unit tests for the route, promote and finalize commands of pcbkit.cli.

The commands only find the project, call pcbkit.route.flow and turn the outcome into an
exit code, so the flow functions are replaced by recorders here (tests/unit/
test_route_flow.py tests them). What matters at this level: pcbnew is required first,
the options reach the flow, and a route that did not come out clean exits 1.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner, Result

from pcbkit.cli import cli
from pcbkit.route import flow
from tests.board_files import TOML, write_file


def invoke(*args: str) -> Result:
    """Run the pcbkit CLI in-process and return the result."""
    return CliRunner().invoke(cli, list(args))


@pytest.fixture
def project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pcbnew: types.ModuleType
) -> Path:
    """Make a project folder current, with a pcbnew that imports."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("argv", [["route"], ["promote"], ["finalize"]])
def test_each_command_needs_pcbnew_and_points_at_setup(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Say `pcbkit setup` before anything else, even outside a project."""
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    monkeypatch.chdir(tmp_path)
    result = invoke(*argv)
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: pcbnew isn't importable here. In the board project, run: pcbkit setup"
    )


@pytest.mark.parametrize("argv", [["route"], ["promote"], ["finalize"]])
def test_each_command_outside_a_project_says_how_to_start_one(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_pcbnew: types.ModuleType,
) -> None:
    """Report the missing pcbkit.toml, not a traceback."""
    monkeypatch.chdir(tmp_path)
    result = invoke(*argv)
    assert result.exit_code == 1
    assert "pcbkit.toml" in result.output and "pcbkit new" in result.output


def test_route_passes_its_options_to_the_flow(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand --eco, --tries and --passes on, and exit 0 for a clean route."""
    seen: dict[str, Any] = {}

    def fake_route(proj: Any, **kwargs: Any) -> Any:
        seen["root"] = proj.root
        seen.update(kwargs)
        return SimpleNamespace(clean=True)

    monkeypatch.setattr(flow, "route", fake_route)
    result = invoke("route", "--eco", "golden", "--tries", "6", "--passes", "40")
    assert result.exit_code == 0, result.output
    assert seen["root"] == project.resolve()
    assert seen["eco"] == Path("golden")
    assert seen["tries"] == 6 and seen["passes"] == 40


def test_route_without_options_leaves_them_to_the_config(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass None for everything not given, so pcbkit.toml's values apply."""
    seen: dict[str, Any] = {}

    def fake_route(proj: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(clean=True)

    monkeypatch.setattr(flow, "route", fake_route)
    assert invoke("route").exit_code == 0
    assert seen == {"eco": None, "tries": None, "passes": None}


def test_route_that_did_not_come_out_clean_exits_1(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail the command when no try was clean, so a script notices."""
    monkeypatch.setattr(flow, "route", lambda proj, **kw: SimpleNamespace(clean=False))
    assert invoke("route").exit_code == 1


def test_promote_calls_the_flow(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run promote on the project."""
    seen: list[Path] = []
    monkeypatch.setattr(flow, "promote", lambda proj: seen.append(proj.root))
    assert invoke("promote").exit_code == 0
    assert seen == [project.resolve()]


@pytest.mark.parametrize(
    ("argv", "render"), [(["finalize"], True), (["finalize", "--no-render"], False)]
)
def test_finalize_maps_no_render_to_the_render_flag(
    project: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], render: bool
) -> None:
    """Ask for renders unless --no-render is given."""
    seen: list[bool] = []
    monkeypatch.setattr(flow, "finalize", lambda proj, render: seen.append(render))
    assert invoke(*argv).exit_code == 0
    assert seen == [render]
