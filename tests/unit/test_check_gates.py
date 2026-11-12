"""Unit tests for the gates: ``gate_of`` and the gate of each built-in check.

``FROZEN`` is the gate of every built-in check function, written out by hand from the
design (a check runs at the earliest gate at which its inputs exist). The test reads the
built-in modules as syntax trees, so a check that is added, renamed or removed fails
until its row here is changed, and a gate is a decision somebody made, not whatever the
fixtures happened to give. The same trees are used to work out the gate from the
fixtures, which holds ``FROZEN`` to the rule, and the plugin test in
``tests/integration/test_check_plugin.py`` holds the plugin to it with a real
collection. The syntax-tree reader follows only fixtures defined in the same module, so
the fixtures the plugin supplies (``nl``, ``board``, ...) are covered by that real
collection test alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pcbkit.check.builtin as builtin
from pcbkit.check import results
from pcbkit.check.results import BUILTIN_GATES, BUILTIN_GROUPS, GATES, gate_of

BUILTIN_DIR = Path(builtin.__file__).resolve().parent

FROZEN = {
    # test_circuit
    "test_circuit::test_symbol_pin_functions_match_datasheet": "sch",
    "test_circuit::test_resistor_power_derated": "sch",
    "test_circuit::test_capacitor_voltage_derated": "sch",
    "test_circuit::test_led_currents": "sch",
    "test_circuit::test_supply_pins_in_range": "sch",
    "test_circuit::test_i2c_addresses_unique_per_bus": "sch",
    "test_circuit::test_i2c_single_pullup_and_sink_current": "sch",
    # test_copper
    "test_copper::test_reserved_copper_stays_clear": "route",
    "test_copper::test_stitching_basis": "sch",
    "test_copper::test_stitching_density": "route",
    "test_copper::test_sensitive_nets_exist": "route",
    "test_copper::test_lmax_basis": "sch",
    "test_copper::test_parallel_length": "route",
    "test_copper::test_filter_caps_sit_by_the_adc_pin": "route",
    "test_copper::test_sense_pair_loop_area": "route",
    "test_copper::test_copper_segment": "route",
    "test_copper::test_load_supply_regulation": "route",
    # test_esp32s3
    "test_esp32s3::test_esp32_reserved_and_uart0_pins_unconnected": "sch",
    "test_esp32s3::test_analog_inputs_are_on_adc1": "sch",
    "test_esp32s3::test_esp32_input_only_pins_have_a_defined_level": "sch",
    "test_esp32s3::test_strapping_pins_at_reset": "sch",
    "test_esp32s3::test_no_esp32_gpio_above_abs_max": "sch",
    "test_esp32s3::test_esp32_gpio_source_current": "sch",
    # test_fab
    "test_fab::test_annular_rings_and_drills_meet_pcbway": "route",
    "test_fab::test_design_rules_cover_fab_limits": "route",
    "test_fab::test_silkscreen_text_is_printable": "route",
    "test_fab::test_assembly_drawing_text_does_not_overlap": "route",
    "test_fab::test_wire_pads_have_polarity_marks": "route",
    "test_fab::test_decoupling_caps_are_close": "place",
    "test_fab::test_power_footprints_match_datasheet": "place",
    "test_fab::test_i2c_rise_time": "route",
    # test_kicad
    "test_kicad::test_erc_clean": "sch",
    "test_kicad::test_drc_clean_with_schematic_parity": "route",
    # test_outputs
    "test_outputs::test_gerber_set_complete": "order",
    "test_outputs::test_job_file_stackup": "order",
    "test_outputs::test_outline_size": "order",
    "test_outputs::test_drill_files_match_board": "order",
    "test_outputs::test_bom_matches_schematic": "order",
    "test_outputs::test_centroid_matches_board": "order",
    "test_outputs::test_bom_complete": "sch",
}


def _is_fixture(node: ast.FunctionDef) -> bool:
    """Return True if the function carries a ``pytest.fixture`` decorator."""
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


def _args(node: ast.FunctionDef) -> list[str]:
    """Return the names of a function's arguments."""
    return [a.arg for a in node.args.posonlyargs + node.args.args]


def read_builtin_checks() -> dict[str, set[str]]:
    """Return ``module::function`` for every built-in check, with the fixtures it needs.

    The fixtures are the check's own arguments, then, repeatedly, the arguments of any
    fixture a module defines among them: the closure pytest's ``item.fixturenames``
    gives, for the fixtures that are written in the module itself.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(BUILTIN_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        fixtures = {f.name: set(_args(f)) for f in functions if _is_fixture(f)}
        for node in functions:
            if not node.name.startswith("test_"):
                continue
            needed = set(_args(node))
            todo = list(needed)
            while todo:
                for name in fixtures.get(todo.pop(), ()):
                    if name not in needed:
                        needed.add(name)
                        todo.append(name)
            found[f"{path.stem}::{node.name}"] = needed
    return found


CHECKS = read_builtin_checks()


def test_the_frozen_table_holds_every_built_in_check_and_nothing_else() -> None:
    """Fail for a check that is new, renamed or gone, naming it."""
    assert CHECKS, "no built-in checks were found: the reader is broken"
    new = sorted(set(CHECKS) - set(FROZEN))
    gone = sorted(set(FROZEN) - set(CHECKS))
    assert not new, f"built-in checks with no gate in FROZEN: {new}"
    assert not gone, f"FROZEN rows for checks that no longer exist: {gone}"


def test_every_built_in_module_is_in_a_group() -> None:
    """Keep the modules the reader found and ``BUILTIN_GROUPS`` the same."""
    assert {key.partition("::")[0] for key in CHECKS} == set(BUILTIN_GROUPS)


@pytest.mark.parametrize("key", sorted(FROZEN))
def test_the_gate_the_rule_gives_is_the_frozen_one(key: str) -> None:
    """Work each gate out from the fixtures and the exceptions, and compare."""
    module, _, function = key.partition("::")
    assert gate_of(module, function, CHECKS[key]) == FROZEN[key]


def test_the_gates_have_the_counts_the_design_gives() -> None:
    """Count the checks at each gate, so a bulk change cannot pass quietly."""
    counts = {gate: list(FROZEN.values()).count(gate) for gate in GATES}
    assert counts == {"sch": 17, "place": 2, "route": 15, "order": 6}


def test_the_exceptions_are_real_checks_whose_gate_the_fixtures_get_wrong() -> None:
    """Keep a row in ``BUILTIN_GATES`` only where the fixtures alone are wrong."""
    assert set(BUILTIN_GATES) <= set(FROZEN)
    for key, gate in BUILTIN_GATES.items():
        module, _, function = key.partition("::")
        assert FROZEN[key] == gate
        assert gate_of(None, function, CHECKS[key]) != gate, key


def test_the_gates_are_in_the_order_the_design_steps_come() -> None:
    """Order the gates sch, place, route, order."""
    assert GATES == ("sch", "place", "route", "order")
    assert results.GATES is GATES


# --- gate_of on its own ------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixtures", "expected"),
    [
        ([], "sch"),
        (["nl", "specs", "record"], "sch"),
        (["board"], "route"),
        (["nl", "board", "record"], "route"),
        (["fab_dir"], "order"),
        (["gerbers", "fab_dir", "board"], "order"),
    ],
)
def test_the_fixtures_give_the_gate(fixtures: list[str], expected: str) -> None:
    """Give ``order`` for the fab files, ``route`` for the board, else ``sch``."""
    assert gate_of("test_x", "test_y", fixtures) == expected
    assert gate_of(None, "test_y", iter(fixtures)) == expected


def test_a_marker_wins_over_the_fixtures_and_the_exceptions() -> None:
    """Take the gate the marker names, whatever else would apply."""
    assert gate_of(None, "test_a", ["board"], "place") == "place"
    assert gate_of(None, "test_a", ["fab_dir"], "sch") == "sch"
    key = "test_kicad::test_drc_clean_with_schematic_parity"
    assert BUILTIN_GATES[key] == "route"
    assert gate_of("test_kicad", key.partition("::")[2], [], "order") == "order"


def test_an_exception_applies_to_the_built_in_module_only() -> None:
    """Leave a project check that shares a built-in's name to the fixtures."""
    name = "test_decoupling_caps_are_close"
    assert gate_of("test_fab", name, ["board"]) == "place"
    assert gate_of(None, name, ["board"]) == "route"


def test_a_marker_that_names_no_gate_is_refused() -> None:
    """Raise a ValueError naming the gates, so a typo cannot move a check quietly."""
    with pytest.raises(
        ValueError, match="unknown gate 'plcae'.*sch, place, route, order"
    ):
        gate_of(None, "test_a", [], "plcae")
