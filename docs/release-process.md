# Release process

How a pcbkit release is made, and how its docs reach the site. The docs are published
per version to GitHub Pages by `.github/workflows/docs.yml`, with
[mike](https://github.com/jimporter/mike): each release gets its own copy of the site, a
version picker lists them, and `latest` points at the newest stable one.

## Once, when the project is first published

These are manual steps for the repository's owner. Nothing in CI does them.

1. Create the GitHub repository `bruk-io/pcbkit` and push `main`.
2. In the repository settings, under **Pages**, set the source to
   **Deploy from a branch**, branch `gh-pages`, folder `/ (root)`. The branch appears
   after the first docs deploy; if it does not exist yet, publish the first release
   (below), let the docs workflow create it, then set Pages.
3. List the Claude plugin in the `bruk-io/knowhere` marketplace: in this repository,
   run the `knowhere-publisher` plugin's `publish-setup` skill and pick URL mode (the
   plugin stays in this repository; knowhere stores its address). It adds
   `.github/workflows/publish-to-knowhere.yml`. Then add a repository secret
   `KNOWHERE_PAT`: a fine-grained token with Contents read and write on
   `bruk-io/knowhere`. From then on, a push to `main` that changes `.claude-plugin/`
   updates the listing.

From then on, CI writes to the `gh-pages` branch on every release: never edit that
branch by hand.

## Every release

1. Set the new version, for example `0.2.0`, in two places: `version` in
   `pyproject.toml` and `version` in `.claude-plugin/plugin.json`. A unit test fails
   while they differ.
2. Add the release to `CHANGELOG.md`: a heading `## [0.2.0] - YYYY-MM-DD` and what
   changed, newest first.
3. Check it builds: `uv run --group docs mkdocs build --strict`, and the tests.
4. Commit, then tag the commit with the version and a leading `v`: `v0.2.0`.
5. Push the commit and the tag, and publish a GitHub release for the tag. Tick
   **Set as a pre-release** for a release candidate.

Publishing the release runs `docs.yml`, which builds the site at that tag and deploys
it.

## What the docs workflow does with a tag

The rules are in `tools/docs_version.py` (and its tests), not in the workflow:

| Release | Docs version | `latest` |
|---|---|---|
| `v0.2.0` | `0.2` | moves to `0.2` |
| `v0.2.1` (a patch) | `0.2`, replacing that version's docs | moves to `0.2` |
| `v0.3.0rc1`, or any release marked pre-release on GitHub | its full version, `0.3.0rc1` | stays where it was |
| `v0.1.4` after `0.2` is out | `0.1` | stays on `0.2`: an older release never takes it back |

The tag without its `v` must match `version` in `pyproject.toml`, or the workflow stops
with a message saying which one to change.

To deploy a tag again (after a failed run, say), run the **docs** workflow by hand from
the Actions tab and give it the tag; tick its pre-release box for a release candidate.

## Things to know

- A release created by another workflow with the default `GITHUB_TOKEN` does not start
  `docs.yml`: GitHub does not run workflows for events made with that token. If you
  automate releases later, create them with a personal access token, or call the docs
  workflow from the release workflow (`workflow_call`).
- Every push to `main` and every pull request builds the docs with `--strict` in
  `ci.yml`, so a broken link or a page missing from the navigation fails there, before
  a release.
- To preview the site locally: `uv run --group docs mkdocs serve`.
