"""Unit tests for pcbkit.route.flow: what route, promote and finalize do, in what order.

Every stage is a fake that records its call: pre, eco, post, the router, DRC, the
silkscreen and the fab export. The silkscreen and the fab export belong to other work
packages, so they are stood in for by modules put in ``sys.modules``; the same tests run
unchanged once the real ones exist.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest

from pcbkit.kicad import cli as kicad_cli
from pcbkit.project import Project, load_project
from pcbkit.route import eco as eco_stage
from pcbkit.route import flow, freerouting
from pcbkit.route import post as post_stage
from pcbkit.route import pre as pre_stage
from pcbkit.route.eco import EcoResult
from pcbkit.route.files import RouteFiles, route_files
from pcbkit.route.post import PostResult
from tests.board_files import TOML, write_file

REPORTS = Path(__file__).resolve().parents[1] / "fixtures" / "reports"

CLEAN = kicad_cli.parse_drc((REPORTS / "drc_clean.rpt").read_text(encoding="utf-8"))
DIRTY = kicad_cli.parse_drc(
    (REPORTS / "drc_clearance_and_holes.rpt").read_text(encoding="utf-8")
)


def report_with(counts: dict[str, int]) -> kicad_cli.DrcReport:
    """Return a DRC report whose categories have the given counts."""
    violations = tuple(
        kicad_cli.Violation(name, "", "error", "", "DRC violations", (), (), ())
        for name, n in counts.items()
        for _ in range(n)
    )
    totals = {
        kicad_cli.DRC_VIOLATIONS: len(violations),
        kicad_cli.DRC_UNCONNECTED: 0,
        kicad_cli.DRC_FOOTPRINT: 0,
    }
    return kicad_cli.DrcReport("my_board.kicad_pcb", totals, violations)


class Stages:
    """The fakes, and the record of what was called on them, in order."""

    def __init__(self, proj: Project, monkeypatch: pytest.MonkeyPatch) -> None:
        """Install every fake for the length of one test."""
        self.proj = proj
        self.files = route_files(proj)
        self.calls: list[str] = []
        self.drc_reports: list[kicad_cli.DrcReport] = []
        self.drc_args: list[dict[str, Any]] = []
        self.runs: list[Any] = []
        self.run_args: list[tuple[int, float]] = []
        self.render: list[bool] = []
        self.said: list[str] = []
        self.tries_made = 0
        monkeypatch.setattr(pre_stage, "pre", self.pre)
        monkeypatch.setattr(eco_stage, "eco", self.eco)
        monkeypatch.setattr(post_stage, "post", self.post)
        monkeypatch.setattr(freerouting, "find_router", self.find_router)
        monkeypatch.setattr(freerouting, "run_router", self.run_router)
        monkeypatch.setattr(kicad_cli, "drc", self.drc)
        silk = types.ModuleType("pcbkit.silk")
        silk.apply_silk = self.apply_silk  # type: ignore[attr-defined]
        fab = types.ModuleType("pcbkit.fab")
        fab.export_fab = self.export_fab  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pcbkit.silk", silk)
        monkeypatch.setitem(sys.modules, "pcbkit.fab", fab)

    def pre(self, proj: Project) -> None:
        """Record the stage before Freerouting."""
        self.calls.append("pre")

    def eco(self, proj: Project, base: Path) -> EcoResult:
        """Record the eco stage and report a small change."""
        self.calls.append(f"eco {base}")
        return EcoResult(("R1",), 2, 30, 4, 1, base)

    def post(self, proj: Project) -> PostResult:
        """Record post, and leave a board that names the try that made it."""
        self.tries_made += 1
        self.calls.append("post")
        self.files.pcb.write_text(f"board {self.tries_made}", encoding="utf-8")
        return PostResult(self.files.pcb, True, 227, 96)

    def find_router(self) -> freerouting.Router:
        """Record the router lookup."""
        self.calls.append("find router")
        return freerouting.Router("java", "freerouting.jar")

    def run_router(
        self,
        files: RouteFiles,
        router: freerouting.Router,
        passes: int,
        stall_timeout_s: float,
    ) -> Any:
        """Record a router run: the next of ``runs``, or a normal one."""
        self.calls.append("run")
        self.run_args.append((passes, stall_timeout_s))
        run = (
            self.runs.pop(0)
            if self.runs
            else freerouting.RouterRun("finished", 0, 1.0, files.log, True)
        )
        if run.ses_written:
            files.ses.write_text(f"ses {self.tries_made + 1}", encoding="utf-8")
        return run

    def drc(self, pcb: Path, report: Path, **kwargs: Any) -> Any:
        """Record a DRC run and answer with the next queued report."""
        parity = bool(kwargs.get("schematic_parity"))
        self.calls.append("drc parity" if parity else "drc")
        self.drc_args.append({"pcb": pcb, "report": report, **kwargs})
        shown = self.drc_reports.pop(0) if self.drc_reports else CLEAN
        Path(report).write_text(f"report {len(self.drc_args)}", encoding="utf-8")
        return SimpleNamespace(report=shown)

    def apply_silk(self, proj: Project, pcb: Path | None = None) -> None:
        """Record the silkscreen stage."""
        self.calls.append("silk")

    def export_fab(self, proj: Project, render: bool = True) -> Any:
        """Record the fab export and its render flag."""
        self.calls.append("export")
        self.render.append(render)
        return SimpleNamespace(bom_lines=55, total_parts=116, files=[])

    def say(self, text: str) -> None:
        """Collect what the flow says."""
        self.said.append(text)


@pytest.fixture
def stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stages]:
    """Return fakes installed over a project with a placed board and a schematic."""
    write_file(tmp_path / "pcbkit.toml", TOML)
    proj = load_project(tmp_path)
    files = route_files(proj)
    files.kicad.mkdir()
    files.placed.write_text("placed", encoding="utf-8")
    files.schematic.write_text("schematic", encoding="utf-8")
    yield Stages(proj, monkeypatch)


def golden_folder(stages: Stages, extra: dict[str, str] | None = None) -> Path:
    """Write the three golden files (and ``extra`` ones) and return the folder."""
    folder = stages.proj.golden_dir
    folder.mkdir()
    contents = {
        "prerouted.kicad_pcb": "golden board",
        "my_board.ses": "golden session",
        "my_board.dsn": "golden dsn",
    }
    contents.update(extra or {})
    for name, text in contents.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


# --- finalize -----------------------------------------------------------------------


def test_finalize_copies_golden_then_runs_the_stages_in_order(stages: Stages) -> None:
    """Copy golden/, post, silk, DRC with parity, then export: in that order."""
    golden_folder(stages)
    flow.finalize(stages.proj, say=stages.say)
    assert stages.calls == ["post", "silk", "drc parity", "export"]
    kicad = stages.files.kicad
    assert (kicad / "prerouted.kicad_pcb").read_text() == "golden board"
    assert (kicad / "my_board.ses").read_text() == "golden session"
    assert (kicad / "my_board.dsn").read_text() == "golden dsn"


def test_finalize_runs_drc_with_schematic_parity_on_the_board_it_made(
    stages: Stages,
) -> None:
    """Pass the parity flag, the board and the report path."""
    golden_folder(stages)
    flow.finalize(stages.proj, say=stages.say)
    (args,) = stages.drc_args
    assert args["schematic_parity"] is True
    assert args["pcb"] == stages.files.pcb
    assert args["report"] == stages.files.drc


def test_finalize_exports_with_renders_by_default(stages: Stages) -> None:
    """Ask for the 3D renders unless told not to."""
    golden_folder(stages)
    flow.finalize(stages.proj, say=stages.say)
    assert stages.render == [True]


def test_finalize_without_renders_asks_the_export_to_skip_them(stages: Stages) -> None:
    """Pass render=False for --no-render."""
    golden_folder(stages)
    flow.finalize(stages.proj, render=False, say=stages.say)
    assert stages.render == [False]


def test_finalize_reports_the_post_drc_and_bom_lines(stages: Stages) -> None:
    """Say what post did, the DRC totals, and the BOM size."""
    golden_folder(stages)
    flow.finalize(stages.proj, say=stages.say)
    assert stages.said == [
        "ses import True\n"
        "zones filled, stitching vias: 227, dropped one-sided vias: 96",
        "DRC: 0 violations, 0 unconnected pads, 0 footprint errors",
        "BOM lines: 55 total parts: 116",
    ]


def test_finalize_stops_before_the_export_when_drc_is_not_clean(
    stages: Stages,
) -> None:
    """Fail with the DRC summary and export nothing: it would be for a bad board."""
    golden_folder(stages)
    stages.drc_reports.append(DIRTY)
    with pytest.raises(click.ClickException, match="nothing was exported"):
        flow.finalize(stages.proj, say=stages.say)
    assert stages.calls == ["post", "silk", "drc parity"]
    assert "DRC: 6 violations, 5 unconnected pads, 0 footprint errors" in stages.said[1]
    assert "unconnected_items" in stages.said[1]


def test_finalize_without_a_golden_route_says_to_promote(stages: Stages) -> None:
    """Name the missing file and `pcbkit promote`; run nothing."""
    with pytest.raises(click.ClickException, match=r"prerouted\.kicad_pcb not found.*"):
        flow.finalize(stages.proj, say=stages.say)
    assert stages.calls == []


def test_finalize_with_golden_but_no_session_names_the_session(stages: Stages) -> None:
    """Require both files of the route."""
    folder = golden_folder(stages)
    (folder / "my_board.ses").unlink()
    with pytest.raises(click.ClickException, match=r"my_board\.ses not found"):
        flow.finalize(stages.proj, say=stages.say)
    assert stages.calls == []


def test_finalize_without_the_schematic_says_to_build(stages: Stages) -> None:
    """Point at `pcbkit build`: DRC parity and the BOM fields need the schematic."""
    golden_folder(stages)
    stages.files.schematic.unlink()
    with pytest.raises(click.ClickException, match=r"pcbkit build"):
        flow.finalize(stages.proj, say=stages.say)
    assert stages.calls == []


def test_finalize_copies_whatever_golden_holds_and_skips_hidden_files(
    stages: Stages,
) -> None:
    """Copy a project file too; leave .DS_Store and sub-folders behind."""
    folder = golden_folder(
        stages, {"prerouted.kicad_pro": "golden rules", ".DS_Store": "junk"}
    )
    (folder / "old").mkdir()
    written = flow.copy_golden(stages.proj)
    names = sorted(path.name for path in written)
    assert names == [
        "my_board.dsn",
        "my_board.ses",
        "prerouted.kicad_pcb",
        "prerouted.kicad_pro",
    ]
    assert not (stages.files.kicad / ".DS_Store").exists()
    assert (stages.files.kicad / "prerouted.kicad_pro").read_text() == "golden rules"


# --- promote ------------------------------------------------------------------------


def routed(stages: Stages, project_file: bool = True) -> None:
    """Leave the files a finished route leaves in kicad/."""
    files = stages.files
    files.prerouted.write_text("route board", encoding="utf-8")
    files.ses.write_text("route session", encoding="utf-8")
    files.dsn.write_text("route dsn", encoding="utf-8")
    files.pcb.write_text("finished board", encoding="utf-8")
    if project_file:
        files.prerouted.with_suffix(".kicad_pro").write_text("rules", encoding="utf-8")


def test_promote_copies_the_route_into_golden(stages: Stages) -> None:
    """Write the prerouted board, the session and the DSN, and nothing else."""
    routed(stages)
    written = flow.promote(stages.proj, say=stages.say)
    golden = stages.proj.golden_dir
    assert sorted(p.name for p in golden.iterdir()) == [
        "my_board.dsn",
        "my_board.ses",
        "prerouted.kicad_pcb",
    ]
    assert (golden / "prerouted.kicad_pcb").read_text() == "route board"
    assert (golden / "my_board.ses").read_text() == "route session"
    assert (golden / "my_board.dsn").read_text() == "route dsn"
    assert sorted(written) == sorted(golden.iterdir())
    assert stages.said == [
        "promoted to golden/: prerouted.kicad_pcb, my_board.ses, my_board.dsn"
    ]


def test_promote_leaves_the_project_file_out_of_golden(stages: Stages) -> None:
    """Keep no project file in golden/, though kicad/ has one beside the route."""
    routed(stages)
    assert stages.files.prerouted.with_suffix(".kicad_pro").is_file()
    flow.promote(stages.proj, say=stages.say)
    assert not list(stages.proj.golden_dir.glob("*.kicad_pro"))


def test_promote_checks_drc_with_parity_on_the_finished_board_first(
    stages: Stages,
) -> None:
    """Run DRC with the schematic before copying anything."""
    routed(stages)
    flow.promote(stages.proj, say=stages.say)
    (args,) = stages.drc_args
    assert args["schematic_parity"] is True
    assert args["pcb"] == stages.files.pcb


def test_promote_refuses_a_board_that_fails_drc_and_copies_nothing(
    stages: Stages,
) -> None:
    """Leave golden/ alone when the DRC is dirty, and show why."""
    routed(stages)
    stages.drc_reports.append(DIRTY)
    with pytest.raises(click.ClickException, match="not promoting") as err:
        flow.promote(stages.proj, say=stages.say)
    assert "6 violations, 5 unconnected pads" in err.value.message
    assert not stages.proj.golden_dir.exists()


def test_promote_leaves_an_existing_golden_alone_when_it_refuses(
    stages: Stages,
) -> None:
    """Never overwrite a route that passed with one that did not."""
    folder = golden_folder(stages)
    routed(stages)
    stages.drc_reports.append(report_with({"clearance": 1}))
    with pytest.raises(click.ClickException, match="not promoting"):
        flow.promote(stages.proj, say=stages.say)
    assert (folder / "prerouted.kicad_pcb").read_text() == "golden board"


@pytest.mark.parametrize("missing", ["prerouted", "ses", "dsn", "pcb"])
def test_promote_without_a_routed_board_says_to_route_first(
    stages: Stages, missing: str
) -> None:
    """Name the missing file; do not run DRC."""
    routed(stages)
    files = stages.files
    {
        "prerouted": files.prerouted,
        "ses": files.ses,
        "dsn": files.dsn,
        "pcb": files.pcb,
    }[missing].unlink()
    with pytest.raises(click.ClickException, match=r"pcbkit route"):
        flow.promote(stages.proj, say=stages.say)
    assert stages.drc_args == []


# --- route --------------------------------------------------------------------------


def test_route_without_a_placed_board_says_to_build_first(stages: Stages) -> None:
    """Fail before looking for Java, with the command to run."""
    stages.files.placed.unlink()
    with pytest.raises(click.ClickException, match=r"run `pcbkit build` first"):
        flow.route(stages.proj, say=stages.say)
    assert stages.calls == []


def test_route_runs_pre_the_router_post_drc_silk_and_the_parity_drc(
    stages: Stages,
) -> None:
    """Follow route.sh: pre, then run/post/DRC per try, then silk and parity DRC."""
    result = flow.route(stages.proj, say=stages.say)
    assert stages.calls == [
        "find router",
        "pre",
        "run",
        "post",
        "drc",
        "silk",
        "drc parity",
    ]
    assert result.clean
    assert result.eco is None
    assert result.drc is CLEAN
    assert result.pcb == stages.files.pcb


def test_route_takes_tries_passes_and_the_stall_timeout_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass [route] freerouting_passes and stall_timeout_s to the router."""
    write_file(
        tmp_path / "pcbkit.toml",
        TOML + "[route]\nfreerouting_passes = 7\ntries = 2\nstall_timeout_s = 33\n",
    )
    proj = load_project(tmp_path)
    files = route_files(proj)
    files.kicad.mkdir()
    files.placed.write_text("placed", encoding="utf-8")
    stages = Stages(proj, monkeypatch)
    stages.drc_reports += [report_with({"clearance": 1})] * 2
    flow.route(proj, say=stages.say)
    assert stages.run_args == [(7, 33.0), (7, 33.0)]  # two tries, then it gives up


def test_route_options_override_the_config(stages: Stages) -> None:
    """Let --tries and --passes win over pcbkit.toml."""
    stages.drc_reports += [report_with({"clearance": 1})] * 4
    flow.route(stages.proj, tries=4, passes=12, say=stages.say)
    assert stages.run_args == [(12, 90.0)] * 4


def test_route_keeps_the_first_clean_try(stages: Stages) -> None:
    """Stop at the clean second try and leave its board in place."""
    stages.drc_reports += [report_with({"unconnected_items": 2}), CLEAN, CLEAN]
    result = flow.route(stages.proj, say=stages.say)
    assert result.clean
    assert [a.status for a in result.loop.attempts] == ["problems", "clean"]
    assert stages.files.pcb.read_text() == "board 2"
    assert stages.files.ses.read_text() == "ses 2"


def test_route_gives_up_with_the_best_board_and_cleans_up_after_itself(
    stages: Stages,
) -> None:
    """Put back the best try's board and session; leave no scratch folder behind."""
    stages.drc_reports += [
        report_with({"clearance": 5}),
        report_with({"clearance": 1}),
        report_with({"clearance": 3}),
        CLEAN,  # the parity DRC at the end
    ]
    result = flow.route(stages.proj, say=stages.say)
    assert not result.clean
    assert result.loop.best is not None and result.loop.best.number == 2
    assert stages.files.pcb.read_text() == "board 2"
    assert stages.files.ses.read_text() == "ses 2"
    assert [p.name for p in stages.files.kicad.glob(".best-*")] == []
    assert any("the best board is" in line for line in stages.said)
    assert stages.calls[-2:] == [
        "silk",
        "drc parity",
    ]  # still finished, as route.sh did


def test_route_with_no_board_made_skips_the_silkscreen(stages: Stages) -> None:
    """Do nothing more when every try stalled: there is no board to finish."""
    stalled = freerouting.RouterRun("stalled", None, 90.0, Path("x"), False, "no start")
    stages.runs += [stalled] * 3
    result = flow.route(stages.proj, say=stages.say)
    assert not result.clean
    assert result.drc is None
    assert "silk" not in stages.calls
    assert "drc parity" not in stages.calls


def test_route_eco_calls_eco_with_the_folder_and_not_pre(stages: Stages) -> None:
    """Keep the old route: eco instead of pre, and say what it kept."""
    base = Path("golden")
    result = flow.route(stages.proj, eco=base, say=stages.say)
    assert stages.calls[:3] == ["find router", f"eco {base}", "run"]
    assert "pre" not in stages.calls
    assert result.eco is not None
    assert stages.said[0].startswith(
        "changed footprints: ['R1'] new pre-route pieces: 2"
    )


def test_route_eco_that_stalls_routes_the_whole_board(stages: Stages) -> None:
    """Run pre once the eco board stalls, and go on from there."""
    stalled = freerouting.RouterRun("stalled", None, 90.0, Path("x"), False, "no start")
    stages.runs.append(stalled)
    result = flow.route(stages.proj, eco=Path("golden"), tries=1, say=stages.say)
    assert stages.calls[:5] == ["find router", "eco golden", "run", "pre", "run"]
    assert result.clean
    assert result.loop.fell_back


def test_a_full_route_that_stalls_does_not_fall_back(stages: Stages) -> None:
    """Fall back only for --eco: a full route has nothing to fall back to."""
    stalled = freerouting.RouterRun("stalled", None, 90.0, Path("x"), False, "no start")
    stages.runs.append(stalled)
    result = flow.route(stages.proj, tries=2, say=stages.say)
    assert stages.calls.count("pre") == 1
    assert not result.loop.fell_back
    assert [a.status for a in result.loop.attempts] == ["stalled", "clean"]


# --- the DRC text -------------------------------------------------------------------


def test_format_drc_shows_the_totals_and_each_category() -> None:
    """Print the three totals, then the count of every category."""
    text = flow.format_drc(DIRTY)
    assert text.splitlines()[0] == (
        "DRC: 6 violations, 5 unconnected pads, 0 footprint errors"
    )
    assert "   1 clearance" in text
    assert "   2 track_dangling" in text
    assert "   5 unconnected_items" in text


def test_format_drc_of_a_clean_report_is_one_line() -> None:
    """Show only the totals when nothing is wrong."""
    assert flow.format_drc(CLEAN) == (
        "DRC: 0 violations, 0 unconnected pads, 0 footprint errors"
    )
