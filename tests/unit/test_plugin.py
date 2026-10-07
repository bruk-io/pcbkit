"""Unit tests that keep the Claude Code plugin in step with the code it describes.

The plugin is plain files at the repo root: a manifest, a hook, skills, agents and eval
cases. ``claude plugin validate`` only reads the manifest, so these tests check what it
leaves alone:

* the hook is registered the way Claude Code reads it, and the script it names exists;
* each skill and agent has the front matter the plugin's design depends on (the
  manual-only order skill, the forked review skill, the read-only researcher);
* every ``pcbkit`` command and option the skills and agents mention exists, so a command
  that is renamed, or an option that is dropped, fails here and not in a user's session;
* every file the skills link to or point at exists, and the eval cases are complete.

Front matter is read by a small parser for the subset of YAML these files use (plain
and quoted scalars, folded blocks, short lists and one-line maps). A file that strays
outside the subset fails with the line, rather than being misread.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import click
import pytest

from pcbkit.cli import cli

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
AGENTS = ROOT / "agents"
EVALS = ROOT / "evals"

# What Claude Code accepts in a skill's front matter (kebab-case) and in a plugin
# agent's (camelCase). The agent fields a plugin ignores are left out on purpose.
SKILL_FIELDS = {
    "name",
    "description",
    "when_to_use",
    "argument-hint",
    "arguments",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "disallowed-tools",
    "model",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
}
AGENT_FIELDS = {
    "name",
    "description",
    "model",
    "effort",
    "maxTurns",
    "tools",
    "disallowedTools",
    "skills",
    "memory",
    "background",
    "omitClaudeMd",
    "isolation",
    "color",
}
PROMPT_FIELDS = {
    "schema_version",
    "name",
    "description",
    "tags",
    "plugins",
    "runs",
    "expected_outcome",
    "model",
    "max_turns",
    "timeout_seconds",
    "allowed_tools",
    "append_system_prompt",
    "env",
}
GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}
DESCRIPTION_LIMIT = 1536  # what Claude Code keeps of description + when_to_use

SKILL_NAMES = [
    "board-workflow",
    "new-board",
    "add-check",
    "order-pcbway",
    "review-board",
]
AGENT_NAMES = ["parts-researcher", "check-writer"]


# --- reading the files ----------------------------------------------------------------


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return a Markdown file's YAML front matter, for the subset these files use.

    Supported: ``key: value`` with a plain or quoted scalar, ``key: >-`` or ``|``
    with indented lines, ``key:`` with ``  - item`` lines, a one-line list
    ``[a, b]`` and a one-line map ``{ a: b }`` of plain scalars. Anything else raises
    ValueError naming the line: a file that drifts out of the subset is noticed rather
    than misread.
    """
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if match is None:
        raise ValueError("no front matter between two --- lines at the top")
    lines = match.group(1).split("\n")
    found: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        pair = re.fullmatch(r"([A-Za-z_][\w-]*):(?: (.*))?", line)
        if pair is None:
            raise ValueError(f"front matter line not understood: {line!r}")
        key, value = pair.group(1), (pair.group(2) or "").strip()
        if key in found:
            raise ValueError(f"front matter key {key!r} appears twice")
        i += 1
        if value in (">", ">-", "|", "|-"):
            block = []
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i]):
                block.append(lines[i].strip())
                i += 1
            found[key] = (" " if value[0] == ">" else "\n").join(b for b in block if b)
        elif value == "":
            items = []
            while i < len(lines) and re.fullmatch(r"  - .+", lines[i]):
                items.append(lines[i][4:].strip())
                i += 1
            if not items:
                raise ValueError(f"front matter key {key!r} has no value")
            found[key] = items
        else:
            found[key] = scalar(value, line)
    return found


def scalar(value: str, line: str) -> Any:
    """Return one front matter value: a string, a list of strings or a dict."""
    if value[0] in "\"'":
        if len(value) < 2 or value[-1] != value[0]:
            raise ValueError(f"unterminated quote in front matter line: {line!r}")
        return value[1:-1]
    if value[0] == "[" and value[-1] == "]":
        return [part.strip() for part in value[1:-1].split(",") if part.strip()]
    if value[0] == "{" and value[-1] == "}":
        pairs = [part.split(":", 1) for part in value[1:-1].split(",")]
        if any(len(p) != 2 for p in pairs):
            raise ValueError(f"map not understood in front matter line: {line!r}")
        return {k.strip(): v.strip() for k, v in pairs}
    if value[0] in "[{&*!|>%@`" or ": " in value or " #" in value:
        raise ValueError(f"front matter line is outside the supported subset: {line!r}")
    return value


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


def skill_files() -> dict[str, Path]:
    """Return each skill's SKILL.md by directory name."""
    return {path.parent.name: path for path in sorted(SKILLS.glob("*/SKILL.md"))}


def agent_files() -> dict[str, Path]:
    """Return each agent's file by file name."""
    return {path.stem: path for path in sorted(AGENTS.glob("*.md"))}


def plugin_texts() -> dict[Path, str]:
    """Return every Markdown file a person or Claude reads in the plugin."""
    files = sorted(SKILLS.rglob("*.md")) + sorted(AGENTS.glob("*.md"))
    files += sorted(EVALS.rglob("*.md"))
    return {path: read(path) for path in files if "results" not in path.parts}


# --- what the skills say about the command line ---------------------------------------


FENCE_OR_SPAN = re.compile(r"```[^\n]*\n(.*?)```|`([^`]+)`", re.S)


def code_spans(text: str) -> list[str]:
    """Return every fenced code line and inline code span of Markdown, in order.

    A span that wraps onto the next line is joined with single spaces.
    """
    spans = []
    for found in FENCE_OR_SPAN.finditer(text):
        if found.group(1) is not None:
            spans.extend(found.group(1).splitlines())
        else:
            spans.append(" ".join(found.group(2).split()))
    return spans


def options_of(command: click.Command) -> set[str]:
    """Return every option name a click command takes, ``--help`` included."""
    names = {"-h", "--help"}
    for param in command.params:
        names.update(getattr(param, "opts", []))
        names.update(getattr(param, "secondary_opts", []))
    return names


def command_problems(text: str) -> list[str]:
    """Return what is wrong with the ``pcbkit`` command lines in Markdown ``text``.

    Looks only in code (fences and backticks). A token that is ``pcbkit``, or ends in
    ``/pcbkit`` and is not a URL, is a call. The word after it must be a command of
    the CLI (or a ``<placeholder>``), and each ``-x`` or ``--xyz`` after that, up to
    the next call, one of that command's options.
    """
    commands = cli.commands
    problems = []
    for span in code_spans(text):
        tokens = span.split()
        calls = [
            i
            for i, token in enumerate(tokens)
            if (token == "pcbkit" or token.endswith("/pcbkit")) and "://" not in token
        ]
        for n, start in enumerate(calls):
            end = calls[n + 1] if n + 1 < len(calls) else len(tokens)
            rest = tokens[start + 1 : end]
            if not rest or rest[0].startswith(("-", "<")):
                continue  # `pcbkit --help`, `pcbkit <command>`, or just the program
            name = rest[0]
            if name not in commands:
                problems.append(f"`{span}`: pcbkit has no command {name!r}")
                continue
            known = options_of(commands[name])
            for token in rest[1:]:
                option = token.split("=", 1)[0]
                if option.startswith("-") and option not in known:
                    problems.append(f"`{span}`: `pcbkit {name}` has no option {option}")
    return problems


# --- the manifest and the hook --------------------------------------------------------


def test_the_manifest_names_the_plugin_and_matches_the_package() -> None:
    manifest = json.loads(read(ROOT / ".claude-plugin" / "plugin.json"))
    pyproject = tomllib.loads(read(ROOT / "pyproject.toml"))
    assert manifest["name"] == "pcbkit"
    assert manifest["version"] == pyproject["project"]["version"]
    assert manifest["license"] == pyproject["project"]["license"]
    assert manifest["description"]
    assert manifest["author"]["name"]


def test_nothing_but_the_manifest_is_inside_the_plugin_folder() -> None:
    inside = sorted(p.name for p in (ROOT / ".claude-plugin").iterdir())
    assert inside == ["plugin.json"]


@pytest.mark.parametrize("name", ["CLAUDE.md", "bin"])
def test_the_repo_root_has_no_file_the_plugin_loader_dislikes(name: str) -> None:
    """A root CLAUDE.md draws a validate warning and a root bin/ blocks claude.ai."""
    assert not (ROOT / name).exists()


def test_the_hook_runs_the_guard_before_edit_and_write() -> None:
    config = json.loads(read(ROOT / "hooks" / "hooks.json"))
    assert list(config) == ["hooks"]
    assert list(config["hooks"]) == ["PreToolUse"]
    (group,) = config["hooks"]["PreToolUse"]
    assert group["matcher"] == "Edit|Write"
    (handler,) = group["hooks"]
    assert handler["type"] == "command"
    assert handler["command"] == "python3"
    (script,) = handler["args"]
    assert script == "${CLAUDE_PLUGIN_ROOT}/hooks/guard_generated.py"
    assert (ROOT / script.replace("${CLAUDE_PLUGIN_ROOT}/", "")).is_file()


# --- skills ---------------------------------------------------------------------------


def test_the_plugin_has_exactly_the_planned_skills_and_agents() -> None:
    assert sorted(skill_files()) == sorted(SKILL_NAMES)
    assert sorted(agent_files()) == sorted(AGENT_NAMES)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_a_skill_has_a_description_and_only_known_fields(name: str) -> None:
    front = parse_frontmatter(read(skill_files()[name]))
    assert set(front) <= SKILL_FIELDS, set(front) - SKILL_FIELDS
    description = front["description"]
    assert description.strip()
    assert len(description) + len(front.get("when_to_use", "")) <= DESCRIPTION_LIMIT


def test_the_order_skill_is_manual_only() -> None:
    front = parse_frontmatter(read(skill_files()["order-pcbway"]))
    assert front["disable-model-invocation"] == "true"
    assert "user-invocable" not in front


def test_the_review_skill_runs_in_a_fork_the_user_waits_for() -> None:
    front = parse_frontmatter(read(skill_files()["review-board"]))
    assert front["context"] == "fork"
    assert front["background"] == "false"
    # No `agent`: the read-only Explore agent could not write the shots folder.
    assert "agent" not in front


@pytest.mark.parametrize("name", ["board-workflow", "new-board", "add-check"])
def test_the_other_skills_may_be_used_by_claude(name: str) -> None:
    front = parse_frontmatter(read(skill_files()[name]))
    assert "disable-model-invocation" not in front
    assert "context" not in front


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_a_skill_body_stays_short(name: str) -> None:
    assert len(read(skill_files()[name]).splitlines()) <= 120


# --- agents ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_an_agent_has_the_fields_a_plugin_agent_honours(name: str) -> None:
    front = parse_frontmatter(read(agent_files()[name]))
    assert set(front) <= AGENT_FIELDS, set(front) - AGENT_FIELDS
    assert front["name"] == name
    assert front["description"].strip()
    assert front["model"] == "sonnet"
    assert int(front["maxTurns"]) > 0


def test_the_researcher_can_read_and_search_but_never_edit() -> None:
    front = parse_frontmatter(read(agent_files()["parts-researcher"]))
    assert front["tools"] == "WebSearch, WebFetch, Read"
    assert "disallowedTools" not in front


def test_the_check_writer_preloads_a_skill_that_exists_and_may_be_preloaded() -> None:
    front = parse_frontmatter(read(agent_files()["check-writer"]))
    assert front["skills"] == ["add-check"]
    skill = parse_frontmatter(read(skill_files()["add-check"]))
    assert "disable-model-invocation" not in skill


# --- the skills match the command line ------------------------------------------------


def test_every_pcbkit_command_and_option_the_plugin_mentions_exists() -> None:
    problems = []
    for path, text in plugin_texts().items():
        problems += [f"{path.relative_to(ROOT)}: {p}" for p in command_problems(text)]
    assert not problems, "\n".join(problems)


def test_the_plugin_mentions_pcbkit_commands_at_all() -> None:
    """Guard the guard: the scan above must really find command lines to judge."""
    seen = set()
    for text in plugin_texts().values():
        for span in code_spans(text):
            seen.update(re.findall(r"\bpcbkit (\w+)", span))
    expected = {"new", "setup", "build", "route", "promote", "finalize", "check"}
    assert expected <= seen, expected - seen


def test_the_command_scan_catches_a_renamed_command_and_a_dropped_option() -> None:
    text = (
        "Run `pcbkit rout` and then\n"
        "```\n"
        "pcbkit route --retries 3\n"
        ".venv/bin/pcbkit shots --no-render --tries 2\n"
        "uvx --from git+https://github.com/bruk-io/pcbkit pcbkit new my-board\n"
        "pcbkit quote --fab-qty 5 --assembled 2\n"
        "```\n"
    )
    assert command_problems(text) == [
        "`pcbkit rout`: pcbkit has no command 'rout'",
        "`pcbkit route --retries 3`: `pcbkit route` has no option --retries",
        "`.venv/bin/pcbkit shots --no-render --tries 2`: `pcbkit shots` has no "
        "option --tries",
    ]


def test_a_code_span_that_wraps_a_line_is_still_read() -> None:
    text = "Use `pcbkit route\n--tries 3 --nope` here.\n"
    assert command_problems(text) == [
        "`pcbkit route --tries 3 --nope`: `pcbkit route` has no option --nope"
    ]


# --- links and paths ------------------------------------------------------------------


def test_every_relative_link_in_a_skill_resolves_and_every_reference_is_linked() -> (
    None
):
    linked = set()
    for name, path in skill_files().items():
        text = read(path)
        for target in re.findall(r"\]\(([^)#\s]+)\)", text):
            if "://" in target:
                continue
            assert (path.parent / target).is_file(), f"{name}: {target} is missing"
            linked.add((path.parent / target).resolve())
    for reference in SKILLS.glob("*/references/*"):
        assert reference.resolve() in linked, f"{reference} is linked from no skill"


def test_every_plugin_root_path_a_skill_or_agent_names_exists() -> None:
    missing = []
    for path, text in plugin_texts().items():
        for target in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+[\w/])", text):
            if not (ROOT / target).exists():
                missing.append(f"{path.relative_to(ROOT)}: {target}")
    assert not missing, "\n".join(missing)


def test_the_plugin_texts_use_plain_hyphens() -> None:
    """Prose is read by people, who get no em dashes (the house style)."""
    dashed = [str(p.relative_to(ROOT)) for p, t in plugin_texts().items() if "—" in t]
    assert not dashed, dashed


# --- eval cases -----------------------------------------------------------------------


def eval_cases() -> list[Path]:
    """Return each eval case folder: a folder under evals/ that holds a prompt.md."""
    return sorted(p.parent for p in EVALS.glob("*/prompt.md"))


def test_the_four_planned_eval_cases_exist() -> None:
    assert len(eval_cases()) == 4


@pytest.mark.parametrize("case", eval_cases(), ids=lambda p: p.name)
def test_an_eval_case_has_a_known_prompt_and_graded_checks(case: Path) -> None:
    prompt = parse_frontmatter(read(case / "prompt.md"))
    assert set(prompt) <= PROMPT_FIELDS, set(prompt) - PROMPT_FIELDS
    assert read(case / "prompt.md").split("\n---\n", 1)[1].strip(), "empty prompt"
    graders = sorted((case / "graders").glob("*.md"))
    assert graders, "a case without a grader fails to load"
    for grader in graders:
        front = parse_frontmatter(read(grader))
        assert front["type"] in GRADER_TYPES, f"{grader.name}: {front['type']}"


@pytest.mark.parametrize("case", eval_cases(), ids=lambda p: p.name)
def test_an_eval_cases_scaffold_script_exists(case: Path) -> None:
    config = case / "case.yaml"
    if not config.is_file():
        return
    for script in re.findall(r"^\s*scaffold_script:\s*(\S+)\s*$", read(config), re.M):
        assert (case / script).is_file(), f"{case.name}: {script} is missing"
