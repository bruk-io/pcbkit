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
) -> Run:
    """Run the project's checks in a child pytest; return it and its results."""
    command = runner.command(root, expression, [*extra, "-p", "fake_nl"])
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
        "complete": True,
    }


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
