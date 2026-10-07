"""Unit tests for tools/docs_version.py: which docs version a release tag deploys.

The docs workflow hands the script's output to mike, so a mistake here either publishes
a pre-release over a stable version's docs or moves `latest` where it must not go.
``tools/`` is not a package, so the script is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "docs_version.py"


def load_script() -> types.ModuleType:
    """Import tools/docs_version.py under its own name."""
    spec = importlib.util.spec_from_file_location("docs_version_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass looks its module up here
    spec.loader.exec_module(module)
    return module


dv = load_script()


def versions_json(*names: str) -> str:
    """Return a mike versions.json that lists these docs versions, newest first."""
    return json.dumps(
        [
            {"version": name, "title": name, "aliases": ["latest"] if i == 0 else []}
            for i, name in enumerate(names)
        ]
    )


def pyproject(tmp_path: Path, version: str = "0.1.0") -> Path:
    """Write a pyproject.toml with this version and return its path."""
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "pcbkit"\nversion = "{version}"\n')
    return path


# --- which docs version a tag deploys -----------------------------------------------


@pytest.mark.parametrize(
    ("tag", "docs", "title"),
    [
        ("v0.1.0", "0.1", "0.1.0"),
        ("v0.1.3", "0.1", "0.1.3"),
        ("v1.12.7", "1.12", "1.12.7"),
    ],
)
def test_a_release_deploys_under_its_minor_version_and_moves_latest(
    tag: str, docs: str, title: str
) -> None:
    """Give `v0.1.3` the docs version 0.1: a patch release replaces its minor's docs."""
    plan = dv.make_plan(tag, False, tag[1:])
    assert (plan.version, plan.title, plan.latest) == (docs, title, True)


@pytest.mark.parametrize("tag", ["v0.1.0rc1", "v0.2.0b2", "v1.0.0a1", "v0.1.1rc12"])
def test_a_tag_with_a_pre_release_suffix_keeps_its_full_version_and_leaves_latest(
    tag: str,
) -> None:
    """Deploy v0.1.0rc1 as 0.1.0rc1: it must not overwrite the docs of 0.1."""
    plan = dv.make_plan(tag, False, tag[1:])
    assert (plan.version, plan.title, plan.latest) == (tag[1:], tag[1:], False)


def test_a_release_marked_as_a_pre_release_leaves_latest_whatever_its_tag_says() -> (
    None
):
    """Take GitHub's flag as well as the suffix, and keep it off the minor's docs.

    A flagged v0.2.0 deployed as 0.2 would put unreleased text under the name of a
    stable version, and `latest` would show it as soon as 0.2 became latest.
    """
    plan = dv.make_plan("v0.2.0", True, "0.2.0")
    assert (plan.version, plan.title, plan.latest) == ("0.2.0", "0.2.0", False)
    flagged_patch = dv.make_plan("v0.1.4", True, "0.1.4", versions_json("0.1"))
    assert (flagged_patch.version, flagged_patch.latest) == ("0.1.4", False)


def test_a_tag_that_is_not_the_projects_version_stops_and_says_which_to_change() -> (
    None
):
    """Name both versions, and the two ways out."""
    with pytest.raises(dv.DocsVersionError) as caught:
        dv.make_plan("v0.2.0", False, "0.1.0")
    message = str(caught.value)
    assert "v0.2.0" in message and "0.2.0" in message
    assert "pyproject.toml says 0.1.0" in message
    assert "plugin.json" in message
    assert "tag v0.1.0 instead" in message


def test_a_pre_release_tag_must_match_the_project_version_exactly_too() -> None:
    """Refuse v0.1.0rc1 when pyproject.toml still says 0.1.0."""
    with pytest.raises(dv.DocsVersionError, match="does not match pyproject.toml"):
        dv.make_plan("v0.1.0rc1", False, "0.1.0")
    with pytest.raises(dv.DocsVersionError, match="does not match pyproject.toml"):
        dv.make_plan("v0.1.0", False, "0.1.0rc1")


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "0.1.0",
        "V0.1.0",
        "v0.1",
        "v0.1.0.1",
        "v0.1.0-rc1",
        "v0.1.0rc",
        "v0.1.0.dev1",
        "v0.1.0.post1",
        "v0.1.0+local",
        "release-0.1.0",
        "refs/tags/v0.1.0",
        "v0.1.0 v0.1.1",
    ],
)
def test_a_tag_that_is_not_a_release_tag_is_refused_with_the_accepted_forms(
    tag: str,
) -> None:
    """Accept only v, three numbers, and optionally a, b or rc with a number."""
    with pytest.raises(dv.DocsVersionError, match="is not a release tag") as caught:
        dv.make_plan(tag, False, "0.1.0")
    assert "v0.1.0rc1" in str(caught.value)


def test_a_tag_with_a_trailing_newline_from_a_shell_is_read_as_the_tag() -> None:
    """Strip whitespace around the tag, as `$(...)` and workflow inputs leave it."""
    assert dv.make_plan("v0.1.0\n", False, "0.1.0").version == "0.1"


# --- latest never moves back --------------------------------------------------------


def test_a_site_with_nothing_deployed_gets_latest() -> None:
    """Treat an empty versions file (the first deploy) as no versions."""
    assert dv.make_plan("v0.1.0", False, "0.1.0", "").latest is True
    assert dv.make_plan("v0.1.0", False, "0.1.0", "  \n").latest is True
    assert dv.make_plan("v0.1.0", False, "0.1.0", "[]").latest is True


def test_a_newer_release_moves_latest_and_so_does_a_patch_of_the_newest() -> None:
    """Move latest to a higher minor version, and to a redeploy of the current one."""
    deployed = versions_json("0.1")
    assert dv.make_plan("v0.2.0", False, "0.2.0", deployed).latest is True
    assert dv.make_plan("v0.1.1", False, "0.1.1", deployed).latest is True
    assert dv.make_plan("v0.1.0", False, "0.1.0", deployed).latest is True


def test_redeploying_an_older_release_leaves_latest_on_the_newer_one() -> None:
    """Keep latest on 0.2 when v0.1.5 is deployed again, and still deploy 0.1."""
    plan = dv.make_plan("v0.1.5", False, "0.1.5", versions_json("0.2", "0.1"))
    assert (plan.version, plan.latest) == ("0.1", False)
    assert "older" in plan.note


def test_versions_are_compared_as_numbers_not_as_text() -> None:
    """Know that 0.10 is newer than 0.9, and 1.0 newer than 0.12."""
    assert dv.make_plan("v0.10.0", False, "0.10.0", versions_json("0.9")).latest
    assert not dv.make_plan("v0.9.9", False, "0.9.9", versions_json("0.10")).latest
    assert not dv.make_plan("v0.12.0", False, "0.12.0", versions_json("1.0")).latest


def test_pre_releases_and_devel_in_the_deployed_list_do_not_count_as_stable() -> None:
    """Move latest to 0.1 though 0.2.0rc1 and devel are up: neither is a release."""
    deployed = versions_json("devel", "0.2.0rc1", "0.1")
    assert dv.make_plan("v0.1.1", False, "0.1.1", deployed).latest is True
    assert dv.stable_versions(deployed) == [(0, 1)]


def test_a_pre_release_never_moves_latest_even_when_nothing_is_deployed() -> None:
    """Leave latest unset for a first release that is an rc, rather than set it."""
    plan = dv.make_plan("v0.1.0rc1", False, "0.1.0rc1", "")
    assert plan.latest is False


def test_a_deployed_file_that_is_not_a_list_of_versions_is_an_error() -> None:
    """Fail loudly on damage: guessing could move latest backwards."""
    with pytest.raises(dv.DocsVersionError, match="not valid JSON"):
        dv.make_plan("v0.1.0", False, "0.1.0", "{oops")
    with pytest.raises(dv.DocsVersionError, match="JSON list"):
        dv.make_plan("v0.1.0", False, "0.1.0", '{"version": "0.2"}')


def test_entries_that_are_not_versions_are_skipped() -> None:
    """Read what mike writes, and ignore an entry with no usable version."""
    text = json.dumps([{"title": "x"}, "0.5", {"version": 7}, {"version": "0.3"}])
    assert dv.stable_versions(text) == [(0, 3)]


# --- reading pyproject.toml ---------------------------------------------------------


def test_the_project_version_is_read_from_pyproject(tmp_path: Path) -> None:
    """Return the string under [project]."""
    assert dv.read_project_version(pyproject(tmp_path, "0.4.2")) == "0.4.2"


def test_this_repos_pyproject_has_a_version_a_tag_can_match() -> None:
    """Read the real pyproject.toml, so the script and the repo cannot drift apart."""
    version = dv.read_project_version(ROOT / "pyproject.toml")
    assert dv.TAG.fullmatch(f"v{version}"), version


def test_a_missing_or_broken_pyproject_is_a_clear_error(tmp_path: Path) -> None:
    """Say what is wrong with the file instead of a traceback."""
    with pytest.raises(dv.DocsVersionError, match="cannot read"):
        dv.read_project_version(tmp_path / "absent.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[project\n")
    with pytest.raises(dv.DocsVersionError, match="not valid TOML"):
        dv.read_project_version(broken)
    bare = tmp_path / "bare.toml"
    bare.write_text('[tool.x]\nname = "y"\n')
    with pytest.raises(dv.DocsVersionError, match=r"no \[project\] version"):
        dv.read_project_version(bare)


# --- the command line ---------------------------------------------------------------


def test_the_command_prints_the_lines_github_reads_into_its_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Write name=value lines on stdout and the sentence on stderr."""
    code = dv.main(["--tag", "v0.1.0", "--pyproject", str(pyproject(tmp_path))])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == "version=0.1\ntitle=0.1.0\nlatest=true\n"
    assert all(re.fullmatch(r"[a-z]+=\S+", line) for line in out.splitlines())
    assert "latest moves to it" in err


def test_a_pre_release_prints_latest_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Say latest=false for an rc, and that it stays put."""
    path = pyproject(tmp_path, "0.1.0rc1")
    assert dv.main(["--tag", "v0.1.0rc1", "--pyproject", str(path)]) == 0
    out, err = capsys.readouterr()
    assert out == "version=0.1.0rc1\ntitle=0.1.0rc1\nlatest=false\n"
    assert "latest stays put" in err


def test_the_prerelease_flag_and_the_deployed_file_reach_the_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pass --prerelease and --deployed through, and treat a missing file as empty."""
    path = pyproject(tmp_path, "0.1.0")
    flagged = ["--tag", "v0.1.0", "--prerelease", "true", "--pyproject", str(path)]
    assert dv.main(flagged) == 0
    assert capsys.readouterr().out.endswith("latest=false\n")

    deployed = tmp_path / "deployed.json"
    deployed.write_text(versions_json("0.2", "0.1"))
    older = ["--tag", "v0.1.0", "--deployed", str(deployed), "--pyproject", str(path)]
    assert dv.main(older) == 0
    assert capsys.readouterr().out == "version=0.1\ntitle=0.1.0\nlatest=false\n"

    absent = ["--tag", "v0.1.0", "--deployed", str(tmp_path / "none.json")]
    assert dv.main([*absent, "--pyproject", str(path)]) == 0
    assert capsys.readouterr().out.endswith("latest=true\n")


def test_a_mismatch_exits_1_with_the_reason_on_stderr_and_nothing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Give the workflow nothing to read as output when the tag is wrong."""
    code = dv.main(["--tag", "v0.2.0", "--pyproject", str(pyproject(tmp_path))])
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert err.startswith("docs_version.py: tag v0.2.0 does not match pyproject.toml")


def test_the_script_runs_as_a_program(tmp_path: Path) -> None:
    """Run the file itself, as the workflow does, and read its exit code and output."""
    path = pyproject(tmp_path)
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", "v0.1.0", "--pyproject", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0
    assert ok.stdout == "version=0.1\ntitle=0.1.0\nlatest=true\n"
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", "v9.9.9", "--pyproject", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    assert bad.stdout == ""
    assert "does not match pyproject.toml" in bad.stderr
