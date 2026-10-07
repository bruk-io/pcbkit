"""End to end: what a new user types, with every real tool.

`pcbkit new my-board`, `pcbkit setup`, then in the project's own `.venv`: `doctor`,
`build`, `route`, `promote`, `finalize`, `check` and `mutants`. Each is a real process
and each must exit 0. What runs for real: uv (it resolves and installs pcbkit's
dependencies on KiCad's Python, so it needs PyPI or a warm uv cache), KiCad and its
pcbnew, kicad-cli, Java, and Freerouting 1.9.0, which opens its window while it routes.
The generated project depends on this checkout (`--pcbkit-source`), because pcbkit is
not on GitHub yet; its `[route] stall_timeout_s` is raised for this run, as other work
on a busy machine can make Freerouting slow to start, and the template's own default
stays at 90.

Run it with any environment that has pcbkit installed (the test only starts processes):

    uv run pytest tests/e2e/test_blinky_flow.py -m e2e -q -s

About two minutes on a quiet Mac, and about four on a busy one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from pcbkit.kicad import env
from pcbkit.project import load_config
from tests.scaffold_files import REPO

pytestmark = pytest.mark.e2e

PCBKIT = [sys.executable, "-c", "from pcbkit.cli import cli; cli()"]
NOISE = re.compile(r"^\d\d:\d\d:\d\d [AP]M: Debug: |stdpbase\.cpp|^$")
STALL_TIMEOUT_S = 300


@dataclass
class Step:
    """One command of the flow: what it was, how it ended and what it said."""

    name: str
    code: int
    seconds: float
    output: str


def tail(text: str, lines: int = 25) -> str:
    """Return the last lines of a command's output, without the wx debug noise."""
    kept = [line for line in text.splitlines() if not NOISE.search(line)]
    return "\n".join(kept[-lines:])


def run_step(
    steps: list[Step], name: str, args: list[str], cwd: Path, timeout: float
) -> Step:
    """Run one command, record it, and fail the test unless it exits 0."""
    started = time.monotonic()
    done = subprocess.run(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )
    step = Step(name, done.returncode, time.monotonic() - started, done.stdout)
    steps.append(step)
    print(f"[{name}] exit {step.code} in {step.seconds:.0f} s")
    assert step.code == 0, f"`{name}` exited {step.code}:\n{tail(step.output, 40)}"
    return step


@pytest.fixture
def machine_has_the_tools() -> None:
    """Skip, saying why, unless the real tools the flow runs are on this machine."""
    java = env.find_java()
    missing = [
        name
        for name, found in (
            ("uv", env.find_uv()),
            ("KiCad's Python", env.find_kicad_python()),
            ("kicad-cli", env.find_kicad_cli()),
            (
                "Java 17 or newer",
                java if java and env.at_least(java.version, (17,)) else None,
            ),
            ("the Freerouting jar", env.find_freerouting_jar()),
        )
        if found is None
    ]
    if missing:
        pytest.skip(
            f"needs {', '.join(missing)}: `pcbkit doctor` says what is missing "
            "and how to install it"
        )


def test_new_setup_build_route_promote_finalize_check_and_mutants(
    tmp_path: Path, machine_has_the_tools: None
) -> None:
    """Make a board, set it up, take it to Gerbers, check it, and plant its mistakes."""
    steps: list[Step] = []
    project = tmp_path / "my-board"
    venv_pcbkit = [str(project / ".venv" / "bin" / "pcbkit")]

    # 1. pcbkit new, from a checkout, since the repository is not published yet
    made = run_step(
        steps,
        "new",
        [*PCBKIT, "new", "my-board", "--pcbkit-source", str(REPO)],
        tmp_path,
        timeout=60,
    )
    assert "Made My Board in my-board/" in made.output
    assert (project / "pcbkit.toml").is_file() and (
        project / "pyproject.toml"
    ).is_file()
    assert not (project / ".venv").exists()
    toml = project / "pcbkit.toml"
    text = toml.read_text(encoding="utf-8")
    assert "stall_timeout_s = 90 " in text  # the template's own default, left alone
    toml.write_text(
        text.replace("stall_timeout_s = 90 ", f"stall_timeout_s = {STALL_TIMEOUT_S}"),
        encoding="utf-8",
    )
    assert load_config(toml).route.stall_timeout_s == STALL_TIMEOUT_S

    # 2. pcbkit setup: .venv on KiCad's Python with pcbkit and pytest, and the router
    ready = run_step(steps, "setup", [*PCBKIT, "setup"], project, timeout=900)
    assert ".venv  imports pcbnew" in ready.output
    assert "Ready. Next:" in ready.output
    assert Path(venv_pcbkit[0]).is_file()
    pyvenv = (project / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    assert "include-system-site-packages = true" in pyvenv and "3.9" in pyvenv

    # 3. from here on, everything runs from the project's own .venv
    seen = run_step(steps, "doctor", [*venv_pcbkit, "doctor"], project, timeout=120)
    assert "All required items are present." in seen.output

    built = run_step(steps, "build", [*venv_pcbkit, "build"], project, timeout=300)
    assert "ERC        0 errors, 0 warnings" in built.output
    assert "placed 3, missing: []" in built.output

    routed = run_step(steps, "route", [*venv_pcbkit, "route"], project, timeout=1500)
    assert "clean" in routed.output
    assert "DRC: 0 violations, 0 unconnected pads, 0 footprint errors" in routed.output
    assert "  warning:" not in routed.output

    kept = run_step(steps, "promote", [*venv_pcbkit, "promote"], project, timeout=300)
    assert "promoted to golden/" in kept.output
    assert sorted(p.name for p in (project / "golden").iterdir()) == [
        "my_board.dsn",
        "my_board.ses",
        "prerouted.kicad_pcb",
    ]

    done = run_step(steps, "finalize", [*venv_pcbkit, "finalize"], project, timeout=900)
    assert "DRC: 0 violations, 0 unconnected pads, 0 footprint errors" in done.output
    assert "BOM lines: 3 total parts: 3" in done.output
    assert "  warning:" not in done.output
    fab = project / "out" / "fab"
    assert (fab / "My_Board_revA_gerbers.zip").stat().st_size > 1000
    assert (fab / "My_Board_revA_BOM.csv").is_file()
    assert (project / "out" / "docs" / "My_Board_revA_render_iso.png").is_file()

    checked = run_step(steps, "check", [*venv_pcbkit, "check"], project, timeout=600)
    assert "11 passed" in checked.output and "skipped" not in checked.output
    results = json.loads(
        (project / "out" / "checks" / "results.json").read_text("utf-8")
    )
    assert results["counts"] == {"passed": 11}

    planted = run_step(
        steps, "mutants", [*venv_pcbkit, "mutants"], project, timeout=900
    )
    assert "control (no edits)" in planted.output and "PASSES" in planted.output
    assert planted.output.count("CAUGHT") == 3 and "MISSED" not in planted.output
    assert "3/3 planted mistakes caught" in planted.output

    print(
        "\n"
        + "\n".join(f"{s.name:<9} exit {s.code}  {s.seconds:5.0f} s" for s in steps)
    )
    assert [s.code for s in steps] == [0] * 9
