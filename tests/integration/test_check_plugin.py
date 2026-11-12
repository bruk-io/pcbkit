"""Integration: the check plugin and built-in checks, run as `pcbkit check` runs them.

Each test writes a small generic project (``tests/check_toy.py``), builds the real
pytest command line of ``pcbkit.check.runner.command`` and runs it in a child process,
then reads ``out/checks/results.json``. Nothing is faked but the netlist: ``fake_nl``
serves a netlist file as the ``nl`` fixture, so no KiCad is needed and these run in the
plain venv.

A built-in check is shown to bite by planting one mistake in the toy board (or in its
specs) and watching exactly that check fail, after the same project without the mistake
has been seen to pass.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pcbkit.check import runner
from pcbkit.check.results import RESULTS_FILE
from tests import check_toy
from tests.check_toy import SPECS, Toy

BUILTIN = "pcbkit.check.builtin"


@dataclass
class Run:
    """One run of the checks: the child process and the results it left."""

    done: subprocess.CompletedProcess[str]
    results: dict[str, Any]

    @property
    def checks(self) -> dict[str, dict[str, Any]]:
        """Return the checks by id."""
        return self.results.get("checks", {})

    def outcome(self, name: str) -> str:
        """Return the outcome of the one check whose id contains ``name``."""
        found = [v["outcome"] for k, v in self.checks.items() if name in k]
        assert len(found) == 1, f"{name}: {len(found)} matches in {sorted(self.checks)}"
        return found[0]

    def entries(self, name: str) -> list[dict[str, Any]]:
        """Return the results entries of every check whose id contains ``name``."""
        found = [v for k, v in self.checks.items() if name in k]
        assert found, f"{name}: no match in {sorted(self.checks)}"
        return found

    def entry(self, name: str) -> dict[str, Any]:
        """Return the results entry of the one check whose id contains ``name``."""
        found = [v for k, v in self.checks.items() if name in k]
        assert len(found) == 1, f"{name}: {len(found)} matches in {sorted(self.checks)}"
        return found[0]


def run(
    root: Path,
    expression: str | None = None,
    extra: tuple[str, ...] = ("-q",),
    cwd: Path | None = None,
    gate: str | None = None,
) -> Run:
    """Run the project's checks in a child pytest; return it and its results."""
    command = runner.command(root, expression, [*extra, "-p", "fake_nl"], gate)
    done = subprocess.run(
        command,
        cwd=cwd or root,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    path = root / "out" / "checks" / RESULTS_FILE
    results = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return Run(done, results)


def checks_file(root: Path, name: str, body: str) -> None:
    """Write a project check module under ``checks/``."""
    folder = root / "checks"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a toy project with no built-in group switched on."""
    return check_toy.write_project(tmp_path / "board", check_toy.good(), groups=[])


# --- the plugin on its own ---------------------------------------------------------


def test_results_hold_every_outcome_with_its_numbers_reason_and_message(
    project: Path,
) -> None:
    """Write one entry per check: outcome, numbers, skip reason and failure message."""
    checks_file(
        project,
        "test_mix.py",
        """
        import pytest

        def test_passes(record):
            record("volts", 3.3)
            record("table", {"a": [1, 2]})

        def test_fails():
            assert 1 + 1 == 3, "arithmetic is broken"

        def test_skips():
            pytest.skip("not on this revision")

        @pytest.fixture
        def broken():
            raise RuntimeError("setup blew up")

        def test_errors(broken):
            pass

        @pytest.mark.parametrize("n", [1, 2])
        def test_param(n):
            assert n
        """,
    )
    result = run(project)
    assert result.done.returncode == 1
    checks = result.checks
    assert set(checks) == {
        "checks/test_mix.py::test_passes",
        "checks/test_mix.py::test_fails",
        "checks/test_mix.py::test_skips",
        "checks/test_mix.py::test_errors",
        "checks/test_mix.py::test_param[1]",
        "checks/test_mix.py::test_param[2]",
    }
    passed = checks["checks/test_mix.py::test_passes"]
    assert passed["outcome"] == "passed"
    assert passed["group"] == "project"
    assert passed["numbers"] == {"volts": 3.3, "table": {"a": [1, 2]}}
    failed = checks["checks/test_mix.py::test_fails"]
    assert failed["outcome"] == "failed"
    assert failed["message"].startswith("AssertionError: arithmetic is broken")
    skipped = checks["checks/test_mix.py::test_skips"]
    assert skipped["outcome"] == "skipped"
    assert skipped["reason"] == "not on this revision"
    errored = checks["checks/test_mix.py::test_errors"]
    assert errored["outcome"] == "error"
    assert "setup blew up" in errored["message"]
    assert result.results["counts"] == {
        "passed": 3,
        "failed": 1,
        "skipped": 1,
        "error": 1,
    }
    assert result.results["exit_status"] == 1
    assert result.results["copper_mm"] == 0.035
    assert result.results["board"]["stem"] == "toy"


def test_check_ids_are_the_same_wherever_pytest_starts(
    project: Path, tmp_path: Path
) -> None:
    """Name a check the same from the project folder as from a folder elsewhere."""
    checks_file(project, "test_one.py", "def test_a():\n    assert True\n")
    here = run(project).checks
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    there = run(project, cwd=elsewhere).checks
    assert set(here) == set(there) == {"checks/test_one.py::test_a"}


# Names no temporary folder of these tests contains: -k matches folder names too.
TWO_CHECKS = (
    "def test_alpha():\n    assert True\n\n\ndef test_omega():\n    assert True\n"
)


def test_a_full_run_records_a_complete_selection(project: Path) -> None:
    """Say in results.json that nothing was left out of a plain run."""
    checks_file(project, "test_two.py", TWO_CHECKS)
    done = run(project)
    assert done.done.returncode == 0, done.done.stdout
    assert done.results["selection"] == {
        "keyword": "",
        "markexpr": "",
        "deselected": 0,
        "gated_out": 0,
        "complete": True,
    }
    assert done.results["gate"] is None
    assert done.results["waiting"] == {}


def test_a_k_run_records_what_it_left_out(project: Path) -> None:
    """Mark a -k run partial, with its expression and how many checks it dropped."""
    checks_file(project, "test_two.py", TWO_CHECKS)
    done = run(project, "alpha")
    assert done.done.returncode == 0, done.done.stdout
    assert set(done.checks) == {"checks/test_two.py::test_alpha"}
    selection = done.results["selection"]
    assert selection["keyword"] == "alpha"
    assert selection["deselected"] == 1
    assert selection["complete"] is False


def test_a_k_run_that_keeps_every_check_is_still_partial(project: Path) -> None:
    """Treat any -k as partial, even one that happens to match every check."""
    checks_file(project, "test_two.py", TWO_CHECKS)
    selection = run(project, "alpha or omega").results["selection"]
    assert selection["deselected"] == 0
    assert selection["complete"] is False


# --- gates ---------------------------------------------------------------------------


@pytest.fixture
def gated_project(project: Path) -> Path:
    """Return a project with the circuit group and one project check at each gate."""
    root = check_toy.write_project(
        project.parent / "gated", check_toy.good(), groups=["circuit"]
    )
    checks_file(root, "test_schematic.py", "def test_nets(nl):\n    assert nl.parts\n")
    checks_file(
        root,
        "test_board_side.py",
        "def test_tracks(board):\n    assert board is not None\n",
    )
    checks_file(
        root,
        "test_fab_side.py",
        """
        import pytest

        @pytest.mark.pcbkit_gate("order")
        def test_files(nl):
            assert nl.parts
        """,
    )
    return root


def test_a_run_at_the_sch_gate_holds_back_the_later_checks_and_is_partial(
    gated_project: Path,
) -> None:
    """Run the schematic checks only, naming each check held back with its gate."""
    done = run(gated_project, gate="sch")
    assert done.done.returncode == 0, done.done.stdout
    results = done.results
    assert results["gate"] == "sch"
    assert results["waiting"] == {
        "checks/test_board_side.py::test_tracks": "route",
        "checks/test_fab_side.py::test_files": "order",
    }
    assert results["selection"]["gated_out"] == len(results["waiting"]) == 2
    assert results["selection"]["deselected"] == 0
    assert results["selection"]["complete"] is False
    assert set(done.checks) == {
        "checks/test_schematic.py::test_nets",
        *(k for k in done.checks if k.startswith(f"{BUILTIN}.test_circuit")),
    }
    assert {e["gate"] for e in done.checks.values()} == {"sch"}
    assert {e["outcome"] for e in done.checks.values()} == {"passed"}


def test_a_run_at_the_route_gate_holds_back_only_the_order_checks(
    gated_project: Path,
) -> None:
    """Keep the board check (it fails here: no pcbnew) and hold back the order one."""
    done = run(gated_project, gate="route")
    assert done.results["gate"] == "route"
    assert done.results["waiting"] == {"checks/test_fab_side.py::test_files": "order"}
    assert done.results["selection"]["gated_out"] == 1
    assert done.results["selection"]["complete"] is False
    assert "checks/test_board_side.py::test_tracks" in done.checks
    assert done.checks["checks/test_board_side.py::test_tracks"]["gate"] == "route"


def test_a_run_with_no_gate_or_at_order_is_complete_with_nothing_waiting(
    gated_project: Path,
) -> None:
    """Write ``gate`` null and no ``waiting`` for no gate; complete either way."""
    for gate in (None, "order"):
        done = run(gated_project, gate=gate)
        assert done.results["gate"] == gate
        assert done.results["waiting"] == {}
        assert done.results["selection"]["gated_out"] == 0
        assert done.results["selection"]["complete"] is True
        gates = {k: e["gate"] for k, e in done.checks.items()}
        assert gates["checks/test_schematic.py::test_nets"] == "sch"
        assert gates["checks/test_board_side.py::test_tracks"] == "route"
        assert gates["checks/test_fab_side.py::test_files"] == "order"


def test_gated_out_checks_are_counted_apart_from_k_deselections(
    gated_project: Path,
) -> None:
    """Count what -k left out as ``deselected``, what the gate held as ``gated_out``."""
    everything = run(gated_project, gate="sch")
    done = run(gated_project, "test_nets or test_tracks", gate="sch")
    selection = done.results["selection"]
    assert set(done.checks) == {"checks/test_schematic.py::test_nets"}
    assert selection["gated_out"] == 2
    # Eight checks reach the sch gate: test_nets and the seven of the circuit group.
    # -k keeps test_nets and leaves the seven out. test_tracks was held back by the
    # gate first, so it is not counted a second time.
    assert len(everything.checks) == 8
    assert selection["deselected"] == 7
    assert selection["keyword"] == "test_nets or test_tracks"


@pytest.fixture
def later_gates_only(project: Path) -> Path:
    """Return a project whose every check is for a later gate than ``sch``."""
    checks_file(
        project,
        "test_board_side.py",
        "def test_tracks(board):\n    assert board is not None\n",
    )
    return project


def test_a_gate_that_holds_back_every_check_exits_zero_and_still_writes_results(
    later_gates_only: Path,
) -> None:
    """Exit 0, not pytest's 5, when the gate alone left nothing to run."""
    done = run(later_gates_only, gate="sch")
    assert done.done.returncode == 0, done.done.stdout
    results = done.results
    assert results["exit_status"] == 0
    assert results["gate"] == "sch"
    assert results["waiting"] == {"checks/test_board_side.py::test_tracks": "route"}
    assert results["selection"]["gated_out"] == 1
    assert results["selection"]["complete"] is False
    assert done.checks == {}


def test_a_run_that_selects_nothing_still_exits_five_without_a_gate_or_with_k(
    later_gates_only: Path,
) -> None:
    """Keep 5 for no gate and nothing to run, and for -k with every check held back."""
    ungated = run(later_gates_only, "test_nothing_has_this_name")
    assert ungated.done.returncode == 5, ungated.done.stdout
    assert ungated.results["gate"] is None
    with_k = run(later_gates_only, "test_nothing_has_this_name", gate="sch")
    assert with_k.done.returncode == 5, with_k.done.stdout
    assert with_k.results["exit_status"] == 5
    assert with_k.results["waiting"] == {
        "checks/test_board_side.py::test_tracks": "route"
    }


def test_a_k_that_matches_nothing_exits_five_at_a_gate_that_held_checks_back(
    gated_project: Path,
) -> None:
    """Keep 5 when -k left out every check the gate let through."""
    done = run(gated_project, "test_nothing_has_this_name", gate="sch")
    assert done.done.returncode == 5, done.done.stdout
    assert done.results["selection"]["gated_out"] == 2
    assert done.results["selection"]["deselected"] == 8


def test_an_item_that_is_not_a_function_is_not_gated_and_does_not_crash(
    gated_project: Path,
) -> None:
    """Run a plugin's own kind of item at every gate instead of reading its fixtures."""
    (gated_project / "checks" / "conftest.py").write_text(
        textwrap.dedent(
            """
            import pytest


            class Plain(pytest.Item):
                def runtest(self):
                    pass

                def reportinfo(self):
                    return self.path, 0, "plain"


            class PlainFile(pytest.File):
                def collect(self):
                    yield Plain.from_parent(self, name="plain")


            def pytest_collect_file(file_path, parent):
                if file_path.suffix == ".plain":
                    return PlainFile.from_parent(parent, path=file_path)
            """
        ),
        encoding="utf-8",
    )
    (gated_project / "checks" / "one.plain").write_text("x\n", encoding="utf-8")
    done = run(gated_project, gate="sch")
    assert done.done.returncode == 0, done.done.stdout + done.done.stderr
    assert "checks/one.plain::plain" in done.checks
    assert done.checks["checks/one.plain::plain"]["outcome"] == "passed"
    assert "checks/one.plain::plain" not in done.results["waiting"]
    assert "checks/test_board_side.py::test_tracks" in done.results["waiting"]


def test_a_marker_that_names_no_gate_is_a_usage_error(gated_project: Path) -> None:
    """Refuse a misspelt gate in a marker, naming the check."""
    checks_file(
        gated_project,
        "test_typo.py",
        """
        import pytest

        @pytest.mark.pcbkit_gate("plcae")
        def test_x():
            pass
        """,
    )
    done = run(gated_project, gate="sch")
    assert done.done.returncode != 0
    assert "unknown gate 'plcae'" in done.done.stdout + done.done.stderr


def test_the_built_in_checks_get_the_gates_the_frozen_table_holds(
    tmp_path: Path,
) -> None:
    """Collect every built-in check for real and hold the plugin to the table.

    The schematic gate with every group on holds back each check later than ``sch``, so
    ``waiting`` is the plugin's own answer, worked out from the fixtures pytest
    collected, for every built-in check with a later gate. Those that ran must be the
    ``sch`` ones.
    """
    from tests.unit.test_check_gates import FROZEN

    everything = ["kicad", "outputs", "fab", "copper", "circuit", "esp32s3"]
    root = check_toy.write_project(tmp_path / "all", check_toy.good(), everything)
    done = run(root, gate="sch")
    prefix = f"{BUILTIN}."
    waiting = {
        k.removeprefix(prefix).partition("[")[0]: g
        for k, g in done.results["waiting"].items()
    }
    assert waiting == {k: g for k, g in FROZEN.items() if g != "sch"}
    ran = {k.removeprefix(prefix).partition("[")[0] for k in done.checks}
    assert ran <= {k for k, g in FROZEN.items() if g == "sch"}
    assert done.results["selection"]["gated_out"] == len(done.results["waiting"])


def test_without_the_plugin_no_results_file_is_written(project: Path) -> None:
    """Write nothing in a plain pytest run: the plugin is loaded by name only.

    The same checks run with the plugin do write the file, so the absence is real.
    """
    checks_file(project, "test_plain.py", "def test_a():\n    assert True\n")
    results = project / "out" / "checks" / RESULTS_FILE
    plain = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "checks"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert plain.returncode == 0, plain.stdout
    assert not results.exists()
    assert not (project / "out").exists()
    with_plugin = run(project)
    assert with_plugin.done.returncode == 0, with_plugin.done.stdout
    assert results.is_file()


def test_the_fixtures_give_the_project_its_modules_and_a_place_for_plots(
    project: Path,
) -> None:
    """Hand a check the project, its modules and a folder for plots."""
    (project / "layout.py").write_text("W, H = 30.0, 20.0\n", encoding="utf-8")
    checks_file(
        project,
        "test_fixtures.py",
        """
        def test_modules(project, specs, circuits, layout, out_dir, record):
            assert project.config.board.stem == "toy"
            assert specs.DEVKIT_REF == "A1"
            assert "DEVKIT_REF" in specs and "NOPE" not in specs
            assert specs.get("NOPE", 7) == 7
            assert callable(circuits.scenario)
            assert (layout.W, layout.H) == (30.0, 20.0)
            assert out_dir == project.root / "out" / "checks" and out_dir.is_dir()
            record("stem", project.config.board.stem)
        """,
    )
    result = run(project)
    assert result.done.returncode == 0, result.done.stdout
    assert result.entry("test_modules")["numbers"] == {"stem": "toy"}


def test_a_missing_spec_fails_the_check_that_asks_for_it_by_name(project: Path) -> None:
    """Fail, naming the spec and the file, rather than skip or crash obscurely."""
    checks_file(
        project,
        "test_missing.py",
        """
        def test_needs_a_spec(specs):
            return specs.NOT_DEFINED

        def test_needs_a_hook(circuits):
            return circuits.no_such_hook

        def test_needs_a_file(layout):
            return layout.W
        """,
    )
    result = run(project)
    assert result.done.returncode == 1
    spec = result.entry("test_needs_a_spec")
    assert spec["outcome"] == "failed"
    assert "specs.NOT_DEFINED is not defined in" in spec["message"]
    assert str(project / "specs.py") in spec["message"]
    hook = result.entry("test_needs_a_hook")
    assert "circuits.no_such_hook is not defined in" in hook["message"]
    absent = result.entry("test_needs_a_file")
    assert "layout.W is needed, but" in absent["message"]
    assert "does not exist" in absent["message"]


def test_parametrising_from_a_missing_spec_fails_instead_of_vanishing(
    project: Path,
) -> None:
    """Keep a check in the run, failing by name, when the table it needs is gone."""
    checks_file(
        project,
        "test_table.py",
        """
        from pcbkit.check.plugin import spec_params

        def pytest_generate_tests(metafunc):
            spec_params(metafunc, "ref", lambda specs: sorted(specs.PINOUT))
            spec_params(metafunc, "gone", lambda specs: sorted(specs.NO_SUCH_TABLE))

        def test_each_part(ref):
            assert ref == "U1"

        def test_each_gone(gone):
            raise AssertionError("must not run: the spec is missing")
        """,
    )
    result = run(project)
    ok = result.entry("test_each_part[U1]")
    assert ok["outcome"] == "passed"
    missing = result.entry("test_each_gone[missing-spec]")
    assert missing["outcome"] == "failed"
    assert "specs.NO_SUCH_TABLE is not defined in" in missing["message"]


def test_built_in_modules_are_collected_only_for_the_groups_switched_on(
    tmp_path: Path,
) -> None:
    """Leave out a group's modules altogether when it is not in [checks] groups."""

    def collected(groups: list[str]) -> set[str]:
        root = check_toy.write_project(
            tmp_path / "-".join(groups or ["none"]), check_toy.good(), groups
        )
        command = runner.command(root, None, ["--collect-only", "-q", "-p", "fake_nl"])
        done = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(root)},
        )
        assert done.returncode in (0, 5), done.stdout + done.stderr
        return {
            found.group(1)
            for line in done.stdout.splitlines()
            if (found := re.match(r"(test_\w+)\.py::", line))
        }

    assert collected([]) == set()
    assert collected(["circuit"]) == {"test_circuit"}
    assert collected(["esp32s3", "kicad"]) == {"test_esp32s3", "test_kicad"}
    everything = ["kicad", "outputs", "fab", "copper", "circuit", "esp32s3"]
    assert collected(everything) == {
        "test_kicad",
        "test_outputs",
        "test_fab",
        "test_copper",
        "test_circuit",
        "test_esp32s3",
    }


def test_the_expression_picks_checks_like_pytests_k(project: Path) -> None:
    """Run only the checks the -k expression matches."""
    checks_file(
        project,
        "test_k.py",
        "def test_alpha():\n    pass\n\ndef test_beta():\n    pass\n",
    )
    result = run(project, "alpha")
    assert set(result.checks) == {"checks/test_k.py::test_alpha"}


def test_a_run_that_selects_nothing_replaces_the_old_results(project: Path) -> None:
    """Leave an empty results file, so an older run is never read as the last one."""
    checks_file(project, "test_old.py", "def test_old():\n    assert True\n")
    first = run(project)
    assert set(first.checks) == {"checks/test_old.py::test_old"}
    second = run(project, "nothing_has_this_name")
    assert second.done.returncode == 5
    assert second.checks == {}
    assert second.results["counts"] == {}


def test_no_project_is_a_usage_error_not_a_traceback(tmp_path: Path) -> None:
    """Say where the plugin looked when there is no pcbkit.toml."""
    empty = tmp_path / "empty"
    empty.mkdir()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "pcbkit.check.plugin",
            "--pcbkit-project",
            str(empty),
        ],
        cwd=empty,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode != 0
    assert "pcbkit.toml" in done.stderr + done.stdout
    assert "Traceback" not in done.stderr


# --- the built-in checks, one planted mistake each ---------------------------------


GROUPS = ["circuit", "esp32s3"]


@dataclass(frozen=True)
class Plant:
    """One mistake to plant and the built-in check that must catch it."""

    what: str
    check: str
    toy: Callable[[Toy], None] = lambda toy: None
    specs: tuple[str, str] | None = None  # (text to find, text to put there)
    says: str = ""  # what the failure message must contain


def _led_shorted(toy: Toy) -> None:
    toy.set_value("R1", "1")


def _second_pullup(toy: Toy) -> None:
    check_toy.resistor(toy, "R7", "4.7k", "SDA", "+3V3")


def _no_pullup(toy: Toy) -> None:
    toy.move_pin("R2", "2", "GND")


def _pin_on_reserved_gpio(toy: Toy) -> None:
    toy.pins["A1"]["6"] = ("NOISY", "IO35")


def _boot_pulled_low(toy: Toy) -> None:
    toy.move_pin("R6", "2", "GND")


def _divider_bottom_missing(toy: Toy) -> None:
    toy.move_pin("R5", "2", None)


def _floating_input_only_pin(toy: Toy) -> None:
    toy.pins["A1"]["6"] = ("FLOAT", "IO35")


PLANTS = [
    Plant(
        "a pin whose datasheet name differs",
        "test_symbol_pin_functions_match_datasheet[U1]",
        specs=('"1": "VDD"', '"1": "VIN"'),
        says="pin, datasheet, symbol",
    ),
    Plant(
        "resistors rated far below their dissipation",
        "test_resistor_power_derated",
        specs=('"default": 0.100', '"default": 1e-9'),
        says="over 50% of rating",
    ),
    Plant(
        "a capacitor rated under its voltage",
        "test_capacitor_voltage_derated",
        specs=('"CAP-10U": 10.0', '"CAP-10U": 3.3'),
        says="C1",
    ),
    Plant(
        "a capacitor with no rating on file",
        "test_capacitor_voltage_derated",
        specs=('CAP_VRATED = {"CAP-10U": 10.0}', "CAP_VRATED = {}"),
        says="no voltage rating on file for C1",
    ),
    Plant(
        "an LED resistor shorted", "test_led_currents", toy=_led_shorted, says="D1 at"
    ),
    Plant(
        "a supply pin below its minimum",
        "test_supply_pins_in_range",
        specs=("(2.7, 5.5)", "(3.4, 5.5)"),
        says="U1 pin 1 sees 3.30 V",
    ),
    Plant(
        "a design range outside a part's limit",
        "test_supply_pins_in_range",
        specs=('5.5, "<=", 6.0', '5.5, "<=", 5.0'),
        says="is false",
    ),
    Plant(
        "two I2C devices with one address",
        "test_i2c_addresses_unique_per_bus",
        specs=("0x3C", "0x40"),
        says="address clash on bus main",
    ),
    Plant(
        "a second pull-up on an I2C line",
        "test_i2c_single_pullup_and_sink_current[main]",
        toy=_second_pullup,
        says="SDA pull-ups",
    ),
    Plant(
        "no pull-up on an I2C line",
        "test_i2c_single_pullup_and_sink_current[main]",
        toy=_no_pullup,
        says="SDA pull-ups",
    ),
    Plant(
        "a net on a reserved GPIO",
        "test_esp32_reserved_and_uart0_pins_unconnected",
        toy=_pin_on_reserved_gpio,
        says="GPIO [35] must stay free",
    ),
    Plant(
        "an analogue input on ADC2",
        "test_analog_inputs_are_on_adc1",
        specs=("{1: 4, 2: 5, 3: 6, 4: 1,", "{1: 4, 2: 5, 3: 6, 4: 11,"),
        says="SENSE is on GPIO11, which is not an ADC1 pin",
    ),
    Plant(
        "a strapping pin held at the wrong level",
        "test_strapping_pins_at_reset",
        toy=_boot_pulled_low,
        says="GPIO0 must read high at reset",
    ),
    Plant(
        "a GPIO over its absolute maximum",
        "test_no_esp32_gpio_above_abs_max",
        toy=_divider_bottom_missing,
        says="GPIO above 3.6 V",
    ),
    Plant(
        "a GPIO sourcing too much current",
        "test_esp32_gpio_source_current",
        toy=_led_shorted,
        says="",
    ),
    Plant(
        "an input-only pin with nothing to hold it",
        "test_esp32_input_only_pins_have_a_defined_level",
        toy=_floating_input_only_pin,
        specs=(
            "PIN_ALIASES = {}",
            "PIN_ALIASES = {}\nINPUT_ONLY = {35}\nRESERVED = set()",
        ),
        says="GPIO35 (FLOAT) floats",
    ),
]


def test_the_toy_board_passes_every_circuit_and_esp32_check(tmp_path: Path) -> None:
    """Pass every check on the board with nothing wrong with it."""
    root = check_toy.write_project(tmp_path / "board", check_toy.good(), GROUPS)
    result = run(root)
    failed = {
        k: v.get("message")
        for k, v in result.checks.items()
        if v["outcome"] != "passed"
    }
    assert result.done.returncode == 0, result.done.stdout
    assert not failed, failed
    # the groups ran: seven circuit checks and nine ESP32 ones (four operating points)
    modules = [k.split("::")[0] for k in result.checks]
    assert modules.count(f"{BUILTIN}.test_circuit") == 7
    assert modules.count(f"{BUILTIN}.test_esp32s3") == 9


@pytest.mark.parametrize("plant", PLANTS, ids=[p.what for p in PLANTS])
def test_each_built_in_check_fails_on_its_planted_mistake(
    plant: Plant, tmp_path: Path
) -> None:
    """Fail the named check, after the project without the mistake has passed it."""
    specs = SPECS
    if plant.specs is not None:
        old, new = plant.specs
        assert old in specs, f"the toy specs no longer contain {old!r}"
        specs = specs.replace(old, new)
    toy = check_toy.good()
    plant.toy(toy)
    # the control: this check, on the board and specs without the mistake, passes
    control = check_toy.write_project(tmp_path / "control", check_toy.good(), GROUPS)
    base = run(control, plant.check.split("[")[0])
    assert {v["outcome"] for v in base.checks.values()} == {"passed"}, base.done.stdout
    # the plant: that check fails, with the message that says why
    root = check_toy.write_project(tmp_path / "plant", toy, GROUPS, specs=specs)
    result = run(root, plant.check.split("[")[0])
    failed = [e for e in result.entries(plant.check) if e["outcome"] == "failed"]
    assert failed, (result.checks, result.done.stdout[-1500:])
    assert all(plant.says in e["message"] for e in failed), [
        e["message"] for e in failed
    ]
    assert result.done.returncode == 1


def test_a_part_that_is_absent_skips_with_its_reason_or_passes_with_a_warning(
    tmp_path: Path,
) -> None:
    """Skip a pinout row for a part the design lacks, if the project says why."""
    specs = SPECS.replace(
        '"U1": {"1": "VDD", "2": "GND", "3": "SDA", "4": "SCL"}}',
        '"U1": {"1": "VDD", "2": "GND", "3": "SDA", "4": "SCL"}, "U9": {"1": "IN"}}',
    )
    assert specs != SPECS
    root = check_toy.write_project(
        tmp_path / "unlisted", check_toy.good(), ["circuit"], specs=specs
    )
    result = run(root, "test_symbol_pin_functions", extra=("-q", "-W", "default"))
    assert result.outcome("[U9]") == "passed"
    assert "U9 is in specs.PINOUT but not in the design" in result.done.stdout
    listed = specs + '\nABSENT_REFS = {"U9": "moved to a module"}\n'
    root = check_toy.write_project(
        tmp_path / "listed", check_toy.good(), ["circuit"], specs=listed
    )
    result = run(root, "test_symbol_pin_functions")
    assert result.outcome("[U9]") == "skipped"
    assert result.entry("[U9]")["reason"] == "moved to a module"


# --- the circuit records and CAP_DERATE ---------------------------------------------

# What the toy's resistor and capacitor checks recorded before the per-part records
# were added: the new records must leave these as they were, key and value.
TOP_FOUR_RESISTORS = '{"R1": 4.55, "R4": 0.14, "R5": 0.07, "R6": 0.0}'
TOP_FOUR_CAPS = '{"C1": [3.3, 10.0]}'


def _tantalum(toy: Toy, pos: str = "VIN", neg: str = "GND") -> None:
    """Add a polarised capacitor, C2, across the toy's 5 V rail (5.5 V at worst)."""
    toy.add(
        "C2",
        "10u",
        "Device:C_Polarized",
        {"1": (pos, "+"), "2": (neg, "-")},
        MPN="TANT-10U",
    )


# the tantalum is rated 9 V, so 5.5 V is 61 % of it: under 80 %, over 50 %
TANT_VRATED = ("CAP_VRATED = {", 'CAP_VRATED = {"TANT-10U": 9.0, ')


def _circuit_run(
    tmp_path: Path,
    name: str,
    toy: Toy | None = None,
    swaps: tuple[tuple[str, str], ...] = (),
    extra: str = "",
    only: str | None = None,
    circuits: str = check_toy.CIRCUITS,
) -> Run:
    """Run the circuit group on a toy with the specs changed as ``swaps`` say."""
    specs = SPECS
    for old, new in swaps:
        assert old in specs, f"the toy specs no longer contain {old!r}"
        specs = specs.replace(old, new)
    root = check_toy.write_project(
        tmp_path / name,
        toy or check_toy.good(),
        ["circuit"],
        specs=specs + extra,
        circuits=circuits,
    )
    return run(root, only)


def test_the_top_four_records_of_the_toy_are_what_they_were_before(
    tmp_path: Path,
) -> None:
    """Keep ``hottest resistor mW`` and ``cap stress (V, rated)`` as they were.

    The value is the same text, and the key still comes first in the entry.
    """
    result = _circuit_run(tmp_path, "records")
    assert result.done.returncode == 0, result.done.stdout
    resistors = result.entry("test_resistor_power_derated")["numbers"]
    assert list(resistors)[0] == "hottest resistor mW"
    assert json.dumps(resistors["hottest resistor mW"]) == TOP_FOUR_RESISTORS
    caps = result.entry("test_capacitor_voltage_derated")["numbers"]
    assert list(caps)[0] == "cap stress (V, rated)"
    assert json.dumps(caps["cap stress (V, rated)"]) == TOP_FOUR_CAPS


def test_every_resistor_capacitor_supply_pin_and_limit_is_recorded(
    tmp_path: Path,
) -> None:
    """Record each part, with the index of the stress scenario that was its worst."""
    result = _circuit_run(tmp_path, "records")
    resistors = result.entry("test_resistor_power_derated")["numbers"]
    power = resistors["resistor power"]
    assert list(power) == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert power["R1"] == {"mW": 4.55, "limit_mW": 50.0, "scenario": 0}
    # the divider sees most at 5.5 V, the second scenario
    assert power["R4"] == {"mW": 0.14, "limit_mW": 50.0, "scenario": 1}
    assert power["R5"]["scenario"] == 1
    caps = result.entry("test_capacitor_voltage_derated")["numbers"]
    assert caps["capacitor voltage"] == {
        "C1": {
            "v": 3.3,
            "rated": 10.0,
            "target": 0.8,
            "limit_v": 8.0,
            "mpn": "CAP-10U",
            "scenario": 0,
        }
    }
    supply = result.entry("test_supply_pins_in_range")["numbers"]
    assert supply["supply pins"] == {"U1 pad 1": {"v": 3.3, "min": 2.7, "max": 5.5}}
    assert supply["supply limits"] == {
        "the rail is under the regulator's input limit": {
            "value": 5.5,
            "op": "<=",
            "limit": 6.0,
            "ok": True,
        }
    }


@pytest.mark.parametrize(
    ("check", "key", "swaps", "says"),
    [
        (
            "test_resistor_power_derated",
            "resistor power",
            (('"default": 0.100', '"default": 1e-9'),),
            "over 50% of rating",
        ),
        (
            "test_supply_pins_in_range",
            "supply pins",
            (("(2.7, 5.5)", "(3.4, 5.5)"),),
            "U1 pin 1 sees 3.30 V",
        ),
        (
            "test_supply_pins_in_range",
            "supply limits",
            (('5.5, "<=", 6.0', '5.5, "<=", 5.0'),),
            "is false",
        ),
    ],
    ids=["resistor over rating", "supply pin below range", "limit broken"],
)
def test_a_failing_check_still_records_its_parts(
    tmp_path: Path, check: str, key: str, swaps: tuple[tuple[str, str], ...], says: str
) -> None:
    """Record before asserting: the failed check's numbers hold every row."""
    result = _circuit_run(tmp_path, "failing", swaps=swaps, only=check)
    entry = result.entry(check)
    assert entry["outcome"] == "failed"
    assert says in entry["message"]
    assert entry["numbers"][key]
    if key == "resistor power":
        assert len(entry["numbers"][key]) == 6
        assert entry["numbers"][key]["R1"]["limit_mW"] == 0.0


def test_a_capacitor_with_no_rating_fails_as_before_and_every_cap_is_recorded(
    tmp_path: Path,
) -> None:
    """Keep the message, and record the rated capacitor and the unrated one."""
    toy = check_toy.good()
    _tantalum(toy)
    result = _circuit_run(tmp_path, "unrated", toy, only="test_capacitor_voltage")
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert "no voltage rating on file for C2 (TANT-10U)" in entry["message"]
    records = entry["numbers"]["capacitor voltage"]
    assert set(records) == {"C1", "C2"}
    assert records["C1"]["rated"] == 10.0
    assert records["C2"] == {
        "v": 5.5,
        "rated": None,
        "target": 0.8,
        "limit_v": None,
        "mpn": "TANT-10U",
        "scenario": 1,
    }
    # as before, the four most stressed are left out when a rating is missing
    assert "cap stress (V, rated)" not in entry["numbers"]


def test_a_reverse_biased_capacitor_fails_as_before_and_every_cap_is_recorded(
    tmp_path: Path,
) -> None:
    """Keep the message, and still record every capacitor, the reversed one too."""
    toy = check_toy.good()
    _tantalum(toy, pos="GND", neg="VIN")
    swaps = (TANT_VRATED,)
    result = _circuit_run(
        tmp_path, "reverse", toy, swaps, only="test_capacitor_voltage"
    )
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert "C2 reverse biased (-5.00 V)" in entry["message"]
    records = entry["numbers"]["capacitor voltage"]
    assert set(records) == {"C1", "C2"}
    # every scenario was solved: the 5.5 V one is the larger, so it is the worst case
    assert records["C2"]["v"] == 5.5
    assert records["C2"]["scenario"] == 1


def test_a_tantalum_at_61_percent_passes_unless_cap_derate_tightens_it(
    tmp_path: Path,
) -> None:
    """Fail the part at 61 % of its rating only when CAP_DERATE asks for 50 %."""
    toy = check_toy.good()
    _tantalum(toy)
    swaps = (TANT_VRATED,)
    control = _circuit_run(
        tmp_path, "control", toy, swaps, only="test_capacitor_voltage"
    )
    assert control.outcome("test_capacitor_voltage_derated") == "passed"
    record = control.entry("test_capacitor_voltage_derated")["numbers"]
    assert record["capacitor voltage"]["C2"]["target"] == 0.8
    assert record["capacitor voltage"]["C2"]["limit_v"] == 7.2
    tight = _circuit_run(
        tmp_path,
        "tight",
        toy,
        swaps,
        extra='\nCAP_DERATE = {"TANT-10U": 0.5}\n',
        only="test_capacitor_voltage",
    )
    entry = tight.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert "{'C2': (5.5, 9.0)}" in entry["message"]
    assert entry["numbers"]["capacitor voltage"]["C2"]["target"] == 0.5
    assert entry["numbers"]["capacitor voltage"]["C2"]["limit_v"] == 4.5
    # the other capacitor keeps 0.8
    assert entry["numbers"]["capacitor voltage"]["C1"]["target"] == 0.8


def test_cap_derate_above_the_default_is_refused_by_name(tmp_path: Path) -> None:
    """Refuse 0.9, naming the table, the row and the shape."""
    toy = check_toy.good()
    _tantalum(toy)
    result = _circuit_run(
        tmp_path,
        "loose",
        toy,
        (TANT_VRATED,),
        extra='\nCAP_DERATE = {"TANT-10U": 0.9}\n',
        only="test_capacitor_voltage",
    )
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert (
        'specs.CAP_DERATE["TANT-10U"]: expected fraction in (0, 0.8], got 0.9'
        in entry["message"]
    )
    assert "Traceback" not in entry["message"]


def test_a_cap_derate_row_with_no_cap_vrated_row_fails_naming_the_mpn(
    tmp_path: Path,
) -> None:
    """Fail when CAP_DERATE names a part number the rating table does not."""
    result = _circuit_run(
        tmp_path,
        "orphan",
        extra='\nCAP_DERATE = {"NO-SUCH-PART": 0.5}\n',
        only="test_capacitor_voltage",
    )
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert (
        "CAP_DERATE names ['NO-SUCH-PART'], which have no row in CAP_VRATED"
        in (entry["message"])
    )
    # the existing capacitor is still recorded
    assert set(entry["numbers"]["capacitor voltage"]) == {"C1"}


# a scenario with vin 13 cannot be solved
UNSOLVABLE = check_toy.CIRCUITS.replace(
    "    circuit = build(nl)\n",
    "    if vin == 13.0:\n"
    '        raise RuntimeError("no convergence")\n'
    "    circuit = build(nl)\n",
)
THREE_SCENARIOS = (
    (
        'STRESS_SCENARIOS = [dict(vin=5.0, drive={"LED_DRV": 3.3}), dict(vin=5.5)]',
        'STRESS_SCENARIOS = [dict(vin=5.0, drive={"LED_DRV": 3.3}), dict(vin=13.0), '
        "dict(vin=5.5)]",
    ),
)


def test_a_scenario_that_cannot_be_solved_after_a_reversal_is_recorded_as_unsolved(
    tmp_path: Path,
) -> None:
    """Keep the message, and name scenario 1; the later scenario is still solved."""
    assert UNSOLVABLE != check_toy.CIRCUITS
    toy = check_toy.good()
    _tantalum(toy, pos="GND", neg="VIN")
    result = _circuit_run(
        tmp_path,
        "unsolved",
        toy,
        (TANT_VRATED, *THREE_SCENARIOS),
        only="test_capacitor_voltage",
        circuits=UNSOLVABLE,
    )
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert "C2 reverse biased (-5.00 V)" in entry["message"]
    assert entry["numbers"]["unsolved scenarios"] == [1]
    # scenario 2 (5.5 V) was solved after the one that was not
    assert entry["numbers"]["capacitor voltage"]["C2"]["scenario"] == 2
    assert entry["numbers"]["capacitor voltage"]["C2"]["v"] == 5.5


def test_a_scenario_that_cannot_be_solved_before_any_reversal_still_raises(
    tmp_path: Path,
) -> None:
    """Keep the old behaviour: no reversal found yet, so the error is the failure."""
    result = _circuit_run(
        tmp_path,
        "raises",
        swaps=THREE_SCENARIOS,
        only="test_capacitor_voltage",
        circuits=UNSOLVABLE,
    )
    entry = result.entry("test_capacitor_voltage_derated")
    assert entry["outcome"] == "failed"
    assert "RuntimeError: no convergence" in entry["message"]
    assert "unsolved scenarios" not in entry["numbers"]


def test_a_supply_limit_that_is_a_numpy_scalar_is_recorded_as_a_plain_number(
    tmp_path: Path,
) -> None:
    """Record ``ok`` as true and the numbers as numbers, not as the text "True"."""
    extra = (
        "\nimport numpy as np\n"
        "SUPPLY_LIMITS = [lambda nl: "
        "('np row', np.float64(5.5), '<=', np.float32(6.0))]\n"
        "SUPPLY_PINS = [('U1', 1, (np.float32(2.7), 5.5))]\n"
    )
    result = _circuit_run(
        tmp_path, "numpy", extra=extra, only="test_supply_pins_in_range"
    )
    entry = result.entry("test_supply_pins_in_range")
    assert entry["outcome"] == "passed", entry
    row = entry["numbers"]["supply limits"]["np row"]
    assert row["ok"] is True
    assert row["value"] == 5.5 and row["limit"] == 6.0
    assert isinstance(row["limit"], float)
    assert isinstance(entry["numbers"]["supply pins"]["U1 pad 1"]["min"], float)


def test_a_supply_limit_row_with_a_bad_operator_is_recorded_as_not_ok(
    tmp_path: Path,
) -> None:
    """Keep the failure, and leave the row in the record."""
    extra = '\nSUPPLY_LIMITS = [("good", 1.0, "<", 2.0), ("odd", 1.0, "==", 2.0)]\n'
    result = _circuit_run(tmp_path, "operator", extra=extra, only="test_supply_pins")
    entry = result.entry("test_supply_pins_in_range")
    assert entry["outcome"] == "failed"
    assert "SUPPLY_LIMITS: 'odd': operator '==' is not < or <=" in entry["message"]
    limits = entry["numbers"]["supply limits"]
    assert limits["good"]["ok"] is True
    assert limits["odd"] == {"value": 1.0, "op": "==", "limit": 2.0, "ok": False}


def test_a_later_supply_limit_that_raises_does_not_hide_a_bad_operator(
    tmp_path: Path,
) -> None:
    """Fail with the first error, the bad operator, not a later row's exception."""
    extra = (
        '\nSUPPLY_LIMITS = [("odd", 1.0, "==", 2.0), lambda nl: 1 / 0, '
        '("text", "a", "<", 2.0)]\n'
    )
    result = _circuit_run(tmp_path, "first", extra=extra, only="test_supply_pins")
    entry = result.entry("test_supply_pins_in_range")
    assert entry["outcome"] == "failed"
    assert "SUPPLY_LIMITS: 'odd': operator '==' is not < or <=" in entry["message"]
    assert "ZeroDivisionError" not in entry["message"]
    assert entry["numbers"]["supply limits"] == {
        "odd": {"value": 1.0, "op": "==", "limit": 2.0, "ok": False}
    }


def test_capacitor_failures_come_in_a_fixed_order(tmp_path: Path) -> None:
    """Report reverse bias, then a missing rating, then an unknown CAP_DERATE part
    number, then a capacitor over its target, each only once the one before is fixed.
    """
    toy = check_toy.good()
    toy.add(
        "C2",
        "10u",
        "Device:C_Polarized",
        {"1": ("GND", "+"), "2": ("VIN", "-")},
        MPN="REV-1",
    )
    toy.add(
        "C3", "1u", "Device:C", {"1": ("VIN", "~"), "2": ("GND", "~")}, MPN="NONE-1"
    )
    toy.add(
        "C4",
        "10u",
        "Device:C_Polarized",
        {"1": ("VIN", "+"), "2": ("GND", "-")},
        MPN="TANT-10U",
    )
    rated = ("CAP_VRATED = {", 'CAP_VRATED = {"REV-1": 50.0, "TANT-10U": 9.0, ')
    derate = '\nCAP_DERATE = {"TANT-10U": 0.5, "NO-SUCH": 0.5}\n'

    def says(
        name: str, toy: Toy, swaps: tuple[tuple[str, str], ...], extra: str
    ) -> str:
        run_ = _circuit_run(
            tmp_path, name, toy, swaps, extra, only="test_capacitor_voltage"
        )
        entry = run_.entry("test_capacitor_voltage_derated")
        assert entry["outcome"] == "failed"
        return entry["message"]

    assert "C2 reverse biased" in says("one", toy, (rated,), derate)
    toy.pins["C2"] = {"1": ("VIN", "+"), "2": ("GND", "-")}
    assert "no voltage rating on file for C3 (NONE-1)" in says(
        "two", toy, (rated,), derate
    )
    both = (
        "CAP_VRATED = {",
        'CAP_VRATED = {"REV-1": 50.0, "TANT-10U": 9.0, "NONE-1": 50.0, ',
    )
    message = says("three", toy, (both,), derate)
    assert "CAP_DERATE names ['NO-SUCH'], which have no row in CAP_VRATED" in message
    assert "C4" not in message  # the capacitor over its target is not named yet
    message = says("four", toy, (both,), '\nCAP_DERATE = {"TANT-10U": 0.5}\n')
    assert "{'C4': (5.5, 9.0)}" in message
