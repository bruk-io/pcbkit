"""Unit tests for the two workflow files that build and publish the docs.

There is no `act` or `actionlint` here, so a workflow cannot be run. What can be done is
to parse each file as GitHub does and pin the keys the docs process depends on: the
trigger, the permission to push gh-pages, the lock against two deploys at once, a full
checkout, the git identity mike needs, and the exact mike commands for a release and
for a pre-release. The commands themselves are shown to work against a throwaway clone
in the work package's report.

PyYAML reads the key ``on`` as the boolean ``True`` (YAML 1.1), which GitHub does not,
so ``triggers`` looks for both.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict[Any, Any]:
    """Return a workflow file parsed as YAML."""
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), name
    return data


def triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    """Return the ``on:`` table, whichever way YAML read its key."""
    found = workflow.get("on", workflow.get(True))
    assert isinstance(found, dict), "the workflow has no `on:` table"
    return found


def steps_of(workflow: dict[Any, Any], job: str) -> list[dict[str, Any]]:
    """Return the steps of one job."""
    return workflow["jobs"][job]["steps"]


def step_named(workflow: dict[Any, Any], job: str, start: str) -> dict[str, Any]:
    """Return the one step of a job whose name starts with ``start``."""
    found = [s for s in steps_of(workflow, job) if s.get("name", "").startswith(start)]
    assert len(found) == 1, (start, [s.get("name") for s in steps_of(workflow, job)])
    return found[0]


def commands(step: dict[str, Any]) -> list[str]:
    """Return the shell commands of a step, one per line, without comments or blanks."""
    lines = [line.strip() for line in step["run"].splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


# --- docs.yml --------------------------------------------------------------------


def test_the_deploy_runs_when_a_release_is_published_and_when_asked_by_hand() -> None:
    """Start on a published release, and on a manual run that names the tag."""
    on = triggers(load("docs.yml"))
    assert set(on) == {"release", "workflow_dispatch"}
    assert on["release"]["types"] == ["published"]
    tag = on["workflow_dispatch"]["inputs"]["tag"]
    assert tag["required"] is True
    assert tag["type"] == "string"


def test_a_manual_run_can_say_the_release_is_a_pre_release() -> None:
    """Offer the flag a release event carries itself, defaulting to a real release."""
    flag = triggers(load("docs.yml"))["workflow_dispatch"]["inputs"]["prerelease"]
    assert flag["type"] == "boolean"
    assert flag["default"] is False


def test_the_deploy_may_push_and_nothing_else() -> None:
    """Grant contents: write, which pushing gh-pages needs, and no other permission."""
    assert load("docs.yml")["permissions"] == {"contents": "write"}


def test_two_deploys_cannot_race_and_one_is_never_cancelled_half_way() -> None:
    """Queue a second deploy behind the first: a cancelled push could tear gh-pages."""
    concurrency = load("docs.yml")["concurrency"]
    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_the_checkout_is_the_tag_and_has_every_branch() -> None:
    """Check out the tag, from the release or the input, with full history for mike."""
    checkout = steps_of(load("docs.yml"), "deploy")[0]
    assert checkout["uses"].startswith("actions/checkout@v")
    assert checkout["with"]["fetch-depth"] == 0
    ref = checkout["with"]["ref"]
    assert "inputs.tag" in ref and "github.event.release.tag_name" in ref


def test_the_job_installs_uv_like_ci_does() -> None:
    """Use astral-sh/setup-uv, as ci.yml does."""
    uses = [s.get("uses", "") for s in steps_of(load("docs.yml"), "deploy")]
    assert any(u.startswith("astral-sh/setup-uv@v") for u in uses)
    ci_uses = [s.get("uses", "") for s in steps_of(load("ci.yml"), "lint")]
    assert any(u.startswith("astral-sh/setup-uv@v") for u in ci_uses)


def test_mike_is_given_a_git_identity_before_it_commits() -> None:
    """Set user.name and user.email before the first mike command."""
    steps = steps_of(load("docs.yml"), "deploy")
    identity = next(
        i
        for i, s in enumerate(steps)
        if "git config user.name" in s.get("run", "")
        and "git config user.email" in s.get("run", "")
    )
    first_mike = next(i for i, s in enumerate(steps) if "mike " in s.get("run", ""))
    assert identity < first_mike


def test_the_version_comes_from_the_tested_script_with_the_tag_and_the_flag() -> None:
    """Run tools/docs_version.py and write its lines to the step's outputs."""
    workflow = load("docs.yml")
    step = step_named(workflow, "deploy", "Work out the docs version")
    assert step["id"] == "docs"
    (call,) = [c for c in commands(step) if "tools/docs_version.py" in c]
    assert '--tag "$TAG"' in call
    assert '--prerelease "$PRERELEASE"' in call
    assert '--deployed "$RUNNER_TEMP/deployed.json"' in call
    assert call.endswith('>> "$GITHUB_OUTPUT"')
    assert (ROOT / "tools" / "docs_version.py").is_file()
    env = step["env"]
    assert "inputs.tag" in env["TAG"] and "release.tag_name" in env["TAG"]
    assert "release.prerelease" in env["PRERELEASE"]
    assert "inputs.prerelease" in env["PRERELEASE"]


def test_the_published_versions_are_read_from_gh_pages_and_may_not_exist() -> None:
    """Read versions.json off gh-pages, or make an empty file when there is none."""
    step = step_named(load("docs.yml"), "deploy", "Work out the docs version")
    (read,) = [c for c in commands(step) if "git show" in c]
    assert "origin/gh-pages:versions.json" in read
    assert '> "$RUNNER_TEMP/deployed.json"' in read
    assert "|| : >" in read, "a first deploy has no gh-pages yet"


def test_a_release_is_deployed_with_the_latest_alias_and_made_the_default() -> None:
    """Run mike deploy --push --update-aliases VERSION latest, then set-default."""
    workflow = load("docs.yml")
    step = step_named(workflow, "deploy", "Deploy the release")
    assert step["if"] == "steps.docs.outputs.latest == 'true'"
    deploy, default = commands(step)
    assert deploy == (
        'uv run --group docs mike deploy --push --update-aliases --title "$TITLE" '
        '"$VERSION" latest'
    )
    assert default == "uv run --group docs mike set-default --push latest"


def test_a_pre_release_is_deployed_without_latest_and_without_a_default() -> None:
    """Deploy the version alone: no alias to move, and no set-default to fail."""
    step = step_named(load("docs.yml"), "deploy", "Deploy the pre-release")
    assert step["if"] == "steps.docs.outputs.latest != 'true'"
    (deploy,) = commands(step)
    assert (
        deploy == 'uv run --group docs mike deploy --push --title "$TITLE" "$VERSION"'
    )
    assert "latest" not in deploy
    assert "--update-aliases" not in deploy
    assert "set-default" not in step["run"]


def test_mike_only_runs_in_the_two_deploy_steps_and_always_pushes() -> None:
    """Keep mike out of every other step, and make each of its commands push."""
    steps = steps_of(load("docs.yml"), "deploy")
    for step in steps:
        for command in commands(step) if "run" in step else []:
            if " mike " in f" {command} ":
                assert " --push " in f" {command} ", command
                assert step["name"].startswith("Deploy"), step["name"]


def test_no_run_script_expands_a_github_expression_inline() -> None:
    """Pass the tag and the outputs through env, never `${{ }}` inside a script.

    A tag name is text from the repository, and an expression in a script is pasted in
    before the shell runs, so a crafted name would run as a command. ci.yml's `test`
    job puts its own matrix value in a script, which is a literal from the same file.
    """
    deploy = load("docs.yml")["jobs"]["deploy"]["steps"]
    docs_job = load("ci.yml")["jobs"]["docs"]["steps"]
    for step in [*deploy, *docs_job]:
        assert "${{" not in step.get("run", ""), step


@pytest.mark.parametrize("name", ["docs.yml", "ci.yml"])
def test_every_action_is_pinned_to_a_major_version(name: str) -> None:
    """Name a major version of each action, not a branch."""
    for job in load(name)["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w./-]+@v\d+", step["uses"]), step["uses"]


def test_docs_yml_never_forces_a_push() -> None:
    """Never overwrite gh-pages: a force push would drop every deployed version."""
    text = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
    assert "--force" not in text
    assert "push -f" not in text


# --- ci.yml ----------------------------------------------------------------------


def test_ci_builds_the_docs_on_main_and_on_pull_requests_without_publishing() -> None:
    """Run a strict docs build on every push to main and every pull request."""
    ci = load("ci.yml")
    on = triggers(ci)
    assert on["push"]["branches"] == ["main"]
    assert "pull_request" in on
    assert "release" not in on
    step = next(s for s in steps_of(ci, "docs") if "mkdocs build" in s.get("run", ""))
    assert step["run"] == "uv run --group docs mkdocs build --strict"


def test_the_ci_docs_job_cannot_publish() -> None:
    """Keep mike, --push and any write permission out of ci.yml."""
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "mike" not in text
    assert "--push" not in text
    assert "contents: write" not in text
    assert "gh-pages" not in text


def test_the_other_ci_jobs_still_need_no_docs_group() -> None:
    """Test and lint without the docs group: CI must not need mkdocs to test."""
    ci = load("ci.yml")
    for job in ("lint", "test"):
        text = str(ci["jobs"][job])
        assert "--group docs" not in text
        assert "--all-groups" not in text
