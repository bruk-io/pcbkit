#!/usr/bin/env python3
"""Decide how a release's docs are deployed: which version, and whether latest moves.

The docs workflow (.github/workflows/docs.yml) runs this on a release tag and hands what
it prints to `mike`. The rules live here, and not in the workflow's YAML, so that they
can be tested.

    docs_version.py --tag v0.1.0 [--prerelease true|false] [--deployed versions.json]

It prints three lines, in the form GitHub Actions reads from ``$GITHUB_OUTPUT``::

    version=0.1
    title=0.1.0
    latest=true

and a sentence saying what it decided on standard error. The rules:

* A tag looks like ``v0.1.0`` or ``v0.1.0rc1``: a ``v``, three numbers, and optionally
  ``a``, ``b`` or ``rc`` with a number. The tag minus its ``v`` must be exactly the
  ``version`` in pyproject.toml, or the script stops and says which one to change.
* A release is a pre-release when its tag has a suffix (``rc1``) or when GitHub's own
  pre-release flag is set (``--prerelease true``). A pre-release is deployed under its
  full version (``0.1.0rc1``, or ``0.2.0`` for a flagged one), so it can never
  overwrite the docs of a stable minor version, and it never moves ``latest``.
* Any other release is deployed under its major and minor numbers (``v0.1.3`` is
  ``0.1``: a patch release replaces its minor version's docs) and moves ``latest``,
  unless ``--deployed`` (the ``versions.json`` of the gh-pages branch) lists a stable
  version that is higher. Redeploying an old release must not move ``latest`` back.

Standard library only (``tomllib``, or ``tomli`` before Python 3.11), and it runs on
Python 3.9 and newer, so it works with whatever ``python3`` a runner has.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# v0.1.0, v1.12.3, v0.1.0rc1, v0.2.0b2, v0.2.0a1: nothing else is a release tag.
TAG = re.compile(
    r"v(?P<version>(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?P<pre>(?:a|b|rc)\d+)?)"
)
# What a stable docs version looks like in versions.json: 0.1, 1.12.
STABLE_DOCS_VERSION = re.compile(r"(\d+)\.(\d+)")

FORMS = "v0.1.0 for a release, v0.1.0rc1 (or a1, b2) for a pre-release"


class DocsVersionError(Exception):
    """A tag, a version or a file that cannot be deployed, with what to do about it."""


@dataclass(frozen=True)
class Plan:
    """How to deploy: the docs ``version``, its ``title``, and whether latest moves.

    ``note`` is the sentence that says why, for the log.
    """

    version: str
    title: str
    latest: bool
    note: str


def parse_tag(tag: str) -> re.Match[str]:
    """Return the match for a release tag, or raise DocsVersionError."""
    found = TAG.fullmatch(tag.strip())
    if found is None:
        raise DocsVersionError(f"{tag!r} is not a release tag. Tags look like {FORMS}.")
    return found


def read_project_version(pyproject: Path) -> str:
    """Return ``[project] version`` from a pyproject.toml, or raise DocsVersionError."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError as err:
        reason = err.strerror or str(err)
        raise DocsVersionError(f"cannot read {pyproject}: {reason}") from None
    except tomllib.TOMLDecodeError as err:
        raise DocsVersionError(f"{pyproject} is not valid TOML: {err}") from None
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise DocsVersionError(f"{pyproject} has no [project] version")
    return version


def stable_versions(deployed: str) -> list[tuple[int, int]]:
    """Return the stable docs versions in a versions.json text, as (major, minor).

    An empty text is a site with nothing deployed. A version that is not plain
    ``major.minor`` (a pre-release's full version, or ``devel``) is not a stable one.
    """
    if not deployed.strip():
        return []
    try:
        entries = json.loads(deployed)
    except json.JSONDecodeError as err:
        raise DocsVersionError(
            f"the deployed versions file is not valid JSON: {err}"
        ) from None
    if not isinstance(entries, list):
        raise DocsVersionError("the deployed versions file should hold a JSON list")
    found = []
    for entry in entries:
        name = entry.get("version") if isinstance(entry, dict) else None
        match = STABLE_DOCS_VERSION.fullmatch(name) if isinstance(name, str) else None
        if match:
            found.append((int(match.group(1)), int(match.group(2))))
    return found


def make_plan(
    tag: str, prerelease: bool, project_version: str, deployed: str = ""
) -> Plan:
    """Return how to deploy the docs of release ``tag``.

    ``project_version`` is the version in pyproject.toml, which the tag must match;
    ``prerelease`` is GitHub's own flag; ``deployed`` is the text of the site's
    versions.json ("" for a site that has none).
    """
    found = parse_tag(tag)
    version = found["version"]
    if version != project_version:
        raise DocsVersionError(
            f"tag {tag} does not match pyproject.toml: the tag says {version}, "
            f"pyproject.toml says {project_version}. Bump the version in "
            f"pyproject.toml (and in .claude-plugin/plugin.json) on the commit you "
            f"tag, or tag v{project_version} instead."
        )
    if found["pre"]:
        return Plan(
            version, version, False, f"{tag} is a pre-release (its tag says so)"
        )
    if prerelease:
        return Plan(version, version, False, f"{tag} is marked as a pre-release")
    docs = (int(found["major"]), int(found["minor"]))
    name = f"{docs[0]}.{docs[1]}"
    if docs < max(stable_versions(deployed), default=docs):
        note = f"{name} is older than a stable version that is already deployed"
        return Plan(name, version, False, note)
    return Plan(name, version, True, f"{tag} is the newest stable release")


def explain(plan: Plan, tag: str) -> str:
    """Return one sentence saying what the plan does and why."""
    if plan.latest:
        return f"{tag}: docs version {plan.version}; latest moves to it ({plan.note})."
    return f"{tag}: docs version {plan.version}; latest stays put ({plan.note})."


def output_lines(plan: Plan) -> str:
    """Return the plan as ``name=value`` lines, the form of ``$GITHUB_OUTPUT``."""
    return (
        f"version={plan.version}\n"
        f"title={plan.title}\n"
        f"latest={'true' if plan.latest else 'false'}\n"
    )


def read_deployed(path: Path | None) -> str:
    """Return the deployed versions file's text; a missing path or file is empty."""
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        description="Decide a release tag's docs version, and whether latest moves."
    )
    parser.add_argument("--tag", required=True, help=f"the release tag: {FORMS}")
    parser.add_argument(
        "--prerelease",
        choices=("true", "false"),
        default="false",
        help="GitHub's pre-release flag for the release (default: false)",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="the pyproject.toml the tag must match (default: ./pyproject.toml)",
    )
    parser.add_argument(
        "--deployed",
        type=Path,
        help="the versions.json of the gh-pages branch; missing or empty means none",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print the plan for a release tag; return 0, or 1 with the reason on stderr."""
    args = build_parser().parse_args(argv)
    try:
        plan = make_plan(
            args.tag,
            args.prerelease == "true",
            read_project_version(args.pyproject),
            read_deployed(args.deployed),
        )
    except DocsVersionError as err:
        print(f"docs_version.py: {err}", file=sys.stderr)
        return 1
    print(explain(plan, args.tag), file=sys.stderr)
    sys.stdout.write(output_lines(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
