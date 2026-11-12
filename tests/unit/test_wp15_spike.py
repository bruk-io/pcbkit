"""Unit tests for tools/wp15_spike/spike.py: the parts that need no KiCad.

The script runs the WP15 spike on a Mac. What is tested here is what decides whether its
report can be believed: the verdict logic, an experiment that raises not stopping the
run, a child that crashes, the report layout, the parsers, and the baseline's
normalisation and folder diff. ``tools/`` is not a package, so the script is loaded by
path.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "wp15_spike" / "spike.py"


def load_script() -> types.ModuleType:
    """Import tools/wp15_spike/spike.py under its own name."""
    spec = importlib.util.spec_from_file_location("wp15_spike_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass looks its module up here
    spec.loader.exec_module(module)
    return module


sp = load_script()


def spec(k: str, run: Any = None, static: Any = None) -> Any:
    """Return a Spec with placeholder texts."""
    return sp.Spec(k, "title " + k, "question?", "assumed", run, static=static)


def ctx(tmp_path: Path) -> Any:
    """Return a Ctx over a temporary folder."""
    return sp.Ctx(tmp_path / "work", tmp_path / "art", sp.Recorder())


# --- importing it needs no KiCad --------------------------------------------------


def test_importing_the_script_does_not_import_pcbnew() -> None:
    code = (
        "import importlib.util, sys\n"
        f"s = importlib.util.spec_from_file_location('x', {str(SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(s); sys.modules['x'] = m\n"
        "s.loader.exec_module(m)\n"
        "print('pcbnew' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT)
    )
    assert done.stdout.strip() == "False", done.stderr


def test_every_k_from_0_to_18_is_listed_once_and_runs_or_is_static() -> None:
    ks = [s.k for s in sp.SPECS]
    assert ks == ["setup"] + [f"K{n}" for n in range(19)]
    for s in sp.SPECS:
        assert (s.run is None) != (s.static is None), s.k
        assert s.question and s.assumed
    assert sp.SPEC_BY_K["K18"].static.verdict == sp.SKIPPED
    assert "ask PCBWay" in sp.SPEC_BY_K["K18"].static.line
    assert "annular ring" in sp.SPEC_BY_K["K18"].static.observed


# --- verdicts -----------------------------------------------------------------------


def test_checks_verdict_rules() -> None:
    def verdict(*states: Any) -> str:
        checks = sp.Checks()
        for i, state in enumerate(states):
            checks.add(f"c{i}", state)
        return checks.verdict()

    assert verdict(True, True) == sp.MATCHES
    assert verdict(True, False) == sp.DIFFERS
    assert verdict(None, False) == sp.DIFFERS  # a broken assumption outranks a gap
    assert verdict(True, None) == sp.FAILED
    assert verdict() == sp.FAILED


def test_checks_summary_names_each_state() -> None:
    checks = sp.Checks()
    checks.add("a", True)
    checks.add("b", False)
    checks.add("c", None)
    text = checks.summary()
    assert "differs: b" in text and "not observed: c" in text and "held: a" in text


# --- an experiment that raises does not stop the run ---------------------------------


def test_an_experiment_that_raises_is_recorded_and_the_rest_run(tmp_path: Path) -> None:
    def good(c: Any) -> Any:
        c.rec.line("raw output of the good one")
        return sp.Outcome(sp.MATCHES, "fine")

    def bad(c: Any) -> Any:
        c.rec.line("got this far")
        raise AttributeError("'BOARD' object has no attribute 'SetLayerType'")

    def skipping(c: Any) -> Any:
        raise sp.Skip("no java")

    specs = [
        spec("K1", good),
        spec("K2", bad),
        spec("K3", skipping),
        spec("K4", good),
    ]
    results = sp.run_all(specs, lambda s: sp.run_experiment(s.run, ctx(tmp_path)))
    assert [r.spec.k for r in results] == ["K1", "K2", "K3", "K4"]
    verdicts = [r.outcome.verdict for r in results]
    assert verdicts == [sp.MATCHES, sp.FAILED, sp.SKIPPED, sp.MATCHES]
    failed = results[1].outcome
    assert "AttributeError" in failed.line and "SetLayerType" in failed.line
    assert "got this far" in failed.observed and "Traceback" in failed.observed
    assert results[2].outcome.line == "no java"
    report = sp.render_report(results, {"Python": "3.9"})
    assert "| K2 | failed |" in report
    assert "## K2: title K2" in report and "Traceback" in report
    assert "## K4: title K4" in report  # the run went on after the failure


def test_a_runner_that_itself_raises_becomes_a_failed_result() -> None:
    def runner(s: Any) -> Any:
        if s.k == "K2":
            raise RuntimeError("boom")
        return sp.Outcome(sp.MATCHES, "ok")

    results = sp.run_all([spec("K1"), spec("K2"), spec("K3")], runner)
    assert [r.outcome.verdict for r in results] == [sp.MATCHES, sp.FAILED, sp.MATCHES]
    assert "boom" in results[1].outcome.line


def test_a_bad_verdict_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    out = sp.run_experiment(lambda c: sp.Outcome("fine", "x"), ctx(tmp_path))
    assert out.verdict == sp.FAILED


def test_a_missing_pcbnew_name_is_recorded_with_close_alternatives(
    tmp_path: Path,
) -> None:
    class Board:
        def GetLayerType(self) -> None:  # noqa: N802
            pass

        def GetLayerName(self) -> None:  # noqa: N802
            pass

    def exp(c: Any) -> Any:
        c.rec.attr(Board(), "SetLayerType", "BOARD")
        return sp.Outcome(sp.MATCHES, "unreachable")

    out = sp.run_experiment(exp, ctx(tmp_path))
    assert out.verdict == sp.FAILED
    assert out.line == "missing name BOARD.SetLayerType"
    assert out.facts["close"][0] == "GetLayerType"
    assert "MISSING BOARD.SetLayerType" in out.observed
    assert "GetLayerType" in out.observed


def test_probe_records_a_missing_name_and_goes_on() -> None:
    rec = sp.Recorder()

    class Via:
        def TopLayer(self) -> int:  # noqa: N802
            return 3

    ok, value = rec.probe(Via(), "TopLayer")
    bad, _ = rec.probe(Via(), "ViaTopLayer")
    assert (ok, value, bad) == (True, 3, False)
    assert "MISSING" in rec.text() and "TopLayer" in rec.text()


def test_alternatives_rank_shared_words_and_skip_private_names() -> None:
    found = sp.alternatives(
        "SetLayerType", ["GetLayerType", "_hidden", "Unrelated", "SetLayerName", "Add"]
    )
    assert "GetLayerType" in found and "SetLayerName" in found
    assert "_hidden" not in found and "Unrelated" not in found


# --- a child that crashes -------------------------------------------------------------


def test_a_child_that_prints_a_result_is_read() -> None:
    payload = json.dumps({"verdict": "matches", "line": "ok", "facts": {"n": 1}})
    code = f"print('noise'); print({sp.RESULT_TAG + ' ' + payload!r})"
    out = sp.run_child([sys.executable, "-c", code], 60)
    assert (out.verdict, out.line, out.facts) == ("matches", "ok", {"n": 1})


@pytest.mark.skipif(sys.platform == "win32", reason="signals")
def test_a_child_killed_by_a_signal_is_a_failure_with_its_partial_output(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "partial.txt"
    code = (
        "import os, signal, sys\n"
        f"open({str(partial)!r}, 'w').write('half way\\n')\n"
        "os.kill(os.getpid(), signal.SIGSEGV)\n"
    )
    out = sp.run_child([sys.executable, "-c", code], 60, partial)
    assert out.verdict == sp.FAILED
    assert "SIGSEGV" in out.line
    assert "half way" in out.observed


def test_a_child_that_overruns_is_a_timeout_failure() -> None:
    out = sp.run_child([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert out.verdict == sp.FAILED and "timed out" in out.line


def test_a_child_without_a_result_line_reports_its_stderr() -> None:
    code = "import sys; sys.stderr.write('bad things\\n'); sys.exit(3)"
    out = sp.run_child([sys.executable, "-c", code], 60)
    assert out.verdict == sp.FAILED and "exit 3" in out.line
    assert "bad things" in out.observed


def test_unmet_tools_become_a_reason() -> None:
    tools = {"pcbnew": "10.0.6", "java": None}
    assert sp.unmet_reason(("pcbnew",), tools) == ""
    why = sp.unmet_reason(("pcbnew", "java", "jar"), tools)
    assert "java not found" in why and "jar not found" in why and "pcbnew" not in why


# --- the parsers ----------------------------------------------------------------------

DSN = """(pcb my_board
  (structure
    (layer F.Cu
      (type signal)
      (property (index 0))
    )
    (layer In1.Cu
      (type power)
      (property (index 1))
    )
    (layer In2.Cu
      (type power)
    )
    (layer B.Cu
      (type signal)
    )
    (plane "/GND" (polygon In1.Cu 0 1 2 3 4 5 6))
    (plane /+5V (polygon In2.Cu 0 1 2 3 4 5 6))
    (keepout "" (polygon F.Cu 0 1 2 3 4 5 6))
    (via "Via[0-3]_700:300_um" "Via[0-3]_700:300_um-1")
  )
)
"""


def test_dsn_summary_reads_layer_types_planes_vias_and_keepouts() -> None:
    got = sp.dsn_summary(DSN)
    assert got["layers"] == [
        ["F.Cu", "signal"],
        ["In1.Cu", "power"],
        ["In2.Cu", "power"],
        ["B.Cu", "signal"],
    ]
    assert got["planes"] == [["/GND", "In1.Cu"], ["/+5V", "In2.Cu"]]
    assert got["via_padstacks"] == ["Via[0-3]_700:300_um"]
    assert got["keepouts"] == 1


def test_dsn_summary_of_a_two_signal_layer_dsn_has_no_power_layer() -> None:
    got = sp.dsn_summary(DSN.replace("(type power)", "(type signal)"))
    assert all(kind == "signal" for _, kind in got["layers"])


SES = """(session my_board.ses
  (base_design my_board.dsn)
  (routes
    (resolution um 10)
    (network_out
      (net "/LED_A"
        (wire (path F.Cu 250 1000 2000 3000 2000))
        (wire (path In1.Cu 250 1000 2000 3000 2000))
      )
      (net "/GND"
        (via "Via[0-3]_700:300_um" 5000 6000)
        (via "Via[0-3]_700:300_um" 7000 6000)
        (wire (path B.Cu 250 1 2 3 4))
      )
    )
  )
)
"""


def test_ses_summary_counts_wires_by_layer_and_vias_per_net() -> None:
    got = sp.ses_summary(SES)
    assert got["/LED_A"]["wires"] == {"F.Cu": 1, "In1.Cu": 1}
    assert got["/GND"] == {"wires": {"B.Cu": 1}, "vias": 2}
    use = sp.ses_layer_use(got, ("GND", "+5V"))
    assert use == {"vias": 2, "wires:B.Cu": 1}


def test_plane_connection_classes() -> None:
    f = sp.classify_plane_connection
    assert f(1, 6, 0, 6, 0) == "tracks on a plane layer"
    assert f(0, 6, 0, 6, 2) == "not connected"
    assert f(0, 6, 0, 6, 0) == "vias to the planes"
    assert f(0, 6, 3, 6, 0) == "vias and tracks"
    assert f(0, 0, 4, 6, 0) == "tracks on the outer layers"
    assert f(0, 0, 0, 6, 0) == "nothing routed"


GOOD_A = {
    "ses_written": True,
    "inner_wires": 0,
    "mode": "vias to the planes",
    "unconnected": 0,
    "repeat_identical": True,
}


def test_k6_matches_only_when_every_assumption_holds() -> None:
    assert sp.judge_k6(GOOD_A).verdict() == sp.MATCHES
    for key, bad in (
        ("inner_wires", 3),
        ("mode", "tracks on the outer layers"),
        ("unconnected", 2),
        ("repeat_identical", False),
        ("ses_written", False),
    ):
        got = sp.judge_k6({**GOOD_A, key: bad})
        assert got.verdict() == sp.DIFFERS, key


def test_job_file_and_k10_judgement() -> None:
    job_text = json.dumps(
        {
            "GeneralSpecs": {"LayerNumber": 4, "BoardThickness": 1.6},
            "MaterialStackup": [
                {"Type": "Copper", "Name": "F.Cu", "Thickness": 0.035},
                {"Type": "Dielectric", "Name": "dielectric 1", "Thickness": 0.2},
                {"Type": "Copper", "Name": "In1.Cu", "Thickness": 0.035},
                {"Type": "Copper", "Name": "In2.Cu", "Thickness": 0.035},
            ],
        }
    )
    job = sp.parse_job(job_text)
    names = ["b-In1_Cu.gbr", "b-In2_Cu.gbr", "b-job.gbrjob"]
    states = dict(sp.judge_k10(names, job, "b"))
    assert all(state is True for state in states.values())
    short = dict(sp.judge_k10(names[:1], job, "b"))
    assert short["b-In2_Cu.gbr written"] is False
    two = dict(sp.judge_k10(names, {**job, "layer_number": 2}, "b"))
    assert two["LayerNumber is 4"] is False


def test_zone_connection_classes() -> None:
    assert sp.classify_connection(16, 16) == "solid"
    assert sp.classify_connection(0, 16) == "not joined"
    assert sp.classify_connection(11, 16).startswith("spokes")


def test_k16_judgement_scales_by_area() -> None:
    assert sp.judge_k16(5.0, 500.0, 6.3)
    assert not sp.judge_k16(20.0, 500.0, 6.3)  # 126 s once scaled
    assert not sp.judge_k16(5.0, 2000.0, 6.3)  # 12.6 GB once scaled


def test_the_four_layer_stackup_adds_up_and_round_trips_through_the_parser() -> None:
    block = sp.four_layer_stackup_text()
    board = "(kicad_pcb\n\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n)\n"
    text = sp.insert_after_setup(board, block)
    rows = sp.stackup_rows(text)
    thickness = sum(
        float(t) for name, kind, t in rows if kind in ("copper", "core", "prepreg")
    )
    assert thickness == pytest.approx(sp.THICKNESS_MM)
    assert [r[0] for r in rows if r[1] == "copper"] == [
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
    ]
    assert [r[1] for r in rows if r[1] in ("core", "prepreg")] == [
        "prepreg",
        "core",
        "prepreg",
    ]
    wider = sp.stackup_rows(
        sp.insert_after_setup(board, sp.four_layer_stackup_text(1.16))
    )
    assert wider != rows  # the K4 mismatch board really differs
    with pytest.raises(ValueError):
        sp.insert_after_setup("(kicad_pcb)", block)


# --- the report ---------------------------------------------------------------------


def results_for(*pairs: Any) -> list[Any]:
    """Return Results from (k, verdict, line, facts) tuples."""
    return [
        sp.Result(spec(k), sp.Outcome(v, line, "raw " + k, (), facts or {}))
        for k, v, line, facts in pairs
    ]


def test_the_report_has_the_table_first_the_sections_then_d3_last() -> None:
    results = results_for(
        ("K1", sp.MATCHES, "ids as assumed", None),
        ("K2", sp.DIFFERS, "a | pipe in the line", None),
        ("K6", sp.SKIPPED, "no java", None),
    )
    text = sp.render_report(results, {"Python": "3.9.13"})
    table = text.index("| K | Verdict | One line |")
    first = text.index("## K1: title K1")
    last = text.index("## D3:")
    assert text.index("# WP15 spike report") < table < first < last
    assert "## K" not in text[last:]  # D3 is the last section
    assert text.rstrip().endswith("Java and the Freerouting jar.")
    assert "| K2 | differs | a \\| pipe in the line |" in text
    assert "**Verdict: skipped.** no java" in text
    assert "```\nraw K1\n```" in text


def test_the_report_fence_outgrows_backticks_in_the_output() -> None:
    results = [
        sp.Result(spec("K1"), sp.Outcome(sp.MATCHES, "x", "has ``` inside", (), {}))
    ]
    assert "````\nhas ``` inside\n````" in sp.section(results[0])


def test_long_output_is_clipped_with_a_note() -> None:
    text = "\n".join(str(i) for i in range(500))
    clipped = sp.clip(text, 10)
    assert clipped.splitlines()[-1].startswith("... (490 more lines")


def test_d3_with_no_k6_says_it_stays_open() -> None:
    text = sp.d3_section(results_for(("K6", sp.SKIPPED, "no jar", None)))
    assert sp.D3_QUESTION in text and "D3 stays open" in text


def test_d3_points_at_a_when_k6_is_clean_and_at_b_when_it_is_not() -> None:
    clean = results_for(
        ("K6", sp.MATCHES, "x", {"a": GOOD_A, "b": None}),
        ("K7", sp.MATCHES, "x", {"inc_in_help": True}),
    )
    assert "build option (a)" in sp.d3_section(clean)
    dirty = {**GOOD_A, "inner_wires": 4}
    bad = results_for(
        ("K6", sp.DIFFERS, "x", {"a": dirty, "b": {**GOOD_A, "inner_wires": 0}}),
        ("K7", sp.MATCHES, "x", {"inc_in_help": True}),
    )
    text = sp.d3_section(bad)
    assert "(b), a fan-out" in text and "4 wires on a power layer" in text
    assert "rule areas" in text and "`-inc` exists" in text


def test_d3_for_a_stalled_router_points_at_b() -> None:
    stalled = {"ses_written": False, "outcome": "stalled", "detail": "no start"}
    text = sp.d3_section(
        results_for(("K6", sp.DIFFERS, "x", {"a": stalled, "b": None}))
    )
    assert "case for (b)" in text


# --- the baseline: normalising and comparing ------------------------------------------

GERBER = (
    b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.6*%\n"
    b"%TF.CreationDate,2026-11-12T10:00:00-05:00*%\n"
    b"X1000Y2000D02*\nX3000Y2000D01*\n"
)


def test_normalise_gerber_drops_only_the_date_lines() -> None:
    other = GERBER.replace(b"2026-11-12T10:00:00", b"2027-01-01T00:00:01")
    assert sp.normalise_gerber(GERBER) == sp.normalise_gerber(other)
    kept = sp.normalise_gerber(GERBER)
    assert b"GenerationSoftware" in kept and b"X3000Y2000D01*" in kept
    assert b"CreationDate" not in kept


def test_normalise_gerber_keeps_a_changed_coordinate_visible() -> None:
    """The planted mistake: a real change of copper must survive the normaliser."""
    moved = GERBER.replace(b"X3000Y2000D01", b"X3001Y2000D01")
    assert sp.normalise_gerber(GERBER) != sp.normalise_gerber(moved)


def test_normalise_gerber_handles_drill_and_job_file_dates_and_odd_bytes() -> None:
    drill = b"M48\n; DRILL file {KiCad 10.0.6} date 2026-11-12T10:00:00-0500\nT1C0.3\n"
    assert b"date" not in sp.normalise_gerber(
        drill
    ) and b"T1C0.3" in sp.normalise_gerber(drill)
    job = b'{\n  "CreationDate": "2026-11-12T10:00:00-05:00",\n  "Rev": "A"\n}\n'
    assert b"CreationDate" not in sp.normalise_gerber(job)
    odd = b"\xff\xfe G04 *\n"
    assert sp.normalise_gerber(odd) == odd


def test_normalise_results_removes_seconds_at_any_depth_and_nothing_else() -> None:
    data = {
        "format": 1,
        "seconds": 12.3,
        "counts": {"passed": 3},
        "checks": {"a": {"outcome": "passed", "seconds": 0.1}},
    }
    first = sp.normalise_results(json.dumps(data).encode())
    data["seconds"] = 99.9
    data["checks"]["a"]["seconds"] = 5
    assert sp.normalise_results(json.dumps(data).encode()) == first
    assert b"seconds" not in first and b'"passed": 3' in first
    data["counts"]["passed"] = 2  # planted: a real change survives
    assert sp.normalise_results(json.dumps(data).encode()) != first


def test_diff_trees_identical_added_removed_and_changed() -> None:
    a = {"x.gbr": b"1\n2\n", "y.dsn": b"same", "only_a": b"a"}
    b = {"x.gbr": b"1\n3\n", "y.dsn": b"same", "only_b": b"b"}
    diff = sp.diff_trees(a, b)
    assert not diff.identical
    assert diff.only_a == ("only_a",) and diff.only_b == ("only_b",)
    (change,) = diff.changed
    assert (change.path, change.first_line, change.a_line, change.b_line) == (
        "x.gbr",
        2,
        "2",
        "3",
    )
    assert not change.uuid_only and diff.same == 1
    assert sp.diff_trees(a, dict(a)).identical


def test_diff_trees_planted_one_byte_change_is_found_and_uuid_noise_is_labelled() -> (
    None
):
    uuid_a = b'(uuid "11111111-2222-3333-4444-555555555555")\n(at 1 2)\n'
    uuid_b = b'(uuid "99999999-2222-3333-4444-555555555555")\n(at 1 2)\n'
    noise = sp.diff_trees({"p.kicad_pcb": uuid_a}, {"p.kicad_pcb": uuid_b})
    assert noise.changed[0].uuid_only
    assert "differs only in UUIDs" in sp.format_diff(noise)
    real = sp.diff_trees(
        {"p.kicad_pcb": uuid_a},
        {"p.kicad_pcb": uuid_a.replace(b"(at 1 2)", b"(at 1 3)")},
    )
    assert not real.changed[0].uuid_only
    assert "changed: p.kicad_pcb, first at line 2" in sp.format_diff(real)


def test_baseline_diff_command_prints_and_sets_the_exit_code(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for folder in (a, b):
        (folder / "02-pre").mkdir(parents=True)
        (folder / "02-pre" / "board.dsn").write_text("same")
        (folder / "commands.log").write_text(folder.name)  # never compared
        (folder / "meta.json").write_text(json.dumps({"pcbkit_commit": folder.name}))
    runner = CliRunner()
    result = runner.invoke(sp.main, ["baseline-diff", str(a), str(b)])
    assert result.exit_code == 0 and "identical: 1 files compared" in result.output
    (b / "02-pre" / "board.dsn").write_text("changed")
    result = runner.invoke(sp.main, ["baseline-diff", str(a), str(b)])
    assert result.exit_code == 1
    assert "changed: 02-pre/board.dsn" in result.output
    assert "A: " in result.output and "pcbkit a" in result.output


def test_stage_files_keeps_only_what_the_plan_lists_normalised(tmp_path: Path) -> None:
    project = tmp_path
    (project / "kicad").mkdir()
    (project / "kicad" / "my_board.kicad_pcb").write_text("pcb")
    (project / "kicad" / "my_board.dsn").write_text("dsn")
    (project / "kicad" / "my_board.net").write_text("net")  # not in the plan's list
    gerbers = project / "out" / "fab" / "gerbers"
    gerbers.mkdir(parents=True)
    (gerbers / "a.gbr").write_bytes(GERBER)
    checks = project / "out" / "checks"
    checks.mkdir(parents=True)
    (checks / "results.json").write_text('{"seconds": 1, "counts": {}}')
    assert sorted(sp.stage_files("build", project)) == [
        "kicad/my_board.dsn",
        "kicad/my_board.kicad_pcb",
    ]
    final = sp.stage_files("finalize", project)
    assert b"CreationDate" not in final["gerbers/a.gbr"]
    assert b"seconds" not in sp.stage_files("check", project)["results.json"]
    assert sp.stage_files("quote", project) == {}
