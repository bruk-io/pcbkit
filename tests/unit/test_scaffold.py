"""Unit tests for pcbkit.scaffold: `pcbkit new`, the template and its drift guard.

Nothing here needs KiCad: a template is files, and `new` copies and renames them. The
tests that read the real template (`pcbkit/templates/board`) and the real example
(`examples/blinky`) are the ones that keep the two honest.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
from click.testing import CliRunner, Result

from pcbkit import bootstrap, scaffold
from pcbkit.cli import cli
from pcbkit.project import NAME_PATTERN, ProjectError, load_config, load_project
from pcbkit.scaffold import Names, ScaffoldError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "blinky"
TEMPLATE = scaffold.TEMPLATE_ROOT / scaffold.TEMPLATES["blinky"]
DOCS = REPO / "docs" / "project-interface.md"
SYNC_SCRIPT = REPO / "examples" / "sync_template.py"
FIX = "uv run python examples/sync_template.py"


def invoke(*args: str, env: dict[str, str] | None = None) -> Result:
    """Run the pcbkit CLI in-process and return the result."""
    return CliRunner().invoke(cli, list(args), env=env)


def make_checkout(root: Path) -> Path:
    """Make a folder that looks like a pcbkit checkout, and return it."""
    (root / "pcbkit").mkdir(parents=True)
    (root / "pcbkit" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pcbkit"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Write text files (paths relative to ``root``), and return ``root``."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def materialise(files: dict[str, bytes], root: Path) -> Path:
    """Write ``files`` (as ``template_files`` returns them) under ``root``."""
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def commands_in(text: str) -> set[str]:
    """Return the pcbkit commands a README shows: at a line's start, or in backticks."""
    at_start = re.findall(r"^(?:\.venv/bin/)?pcbkit (\w+)", text, flags=re.MULTILINE)
    inline = re.findall(r"`(?:\.venv/bin/)?pcbkit (\w+)", text)
    return set(at_start) | set(inline)


def listing(root: Path) -> list[str]:
    """Return every file under ``root``, as sorted ``/`` paths."""
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make an empty folder the current directory, and return it."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


# --- names ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "project", "stem", "title", "fab_name"),
    [
        ("my-board", "my-board", "my_board", "My Board", "My_Board_revA"),
        ("My_Board", "my-board", "my_board", "My Board", "My_Board_revA"),
        (
            "ESP32-carrier",
            "esp32-carrier",
            "esp32_carrier",
            "ESP32 Carrier",
            "ESP32_Carrier_revA",
        ),
        ("blinky", "blinky", "blinky", "Blinky", "Blinky_revA"),
        ("3d-printer", "3d-printer", "3d_printer", "3d Printer", "3d_Printer_revA"),
        ("robot arm", "robot-arm", "robot_arm", "Robot Arm", "Robot_Arm_revA"),
        ("a..b__c--d", "a-b-c-d", "a_b_c_d", "A B C D", "A_B_C_D_revA"),
    ],
)
def test_names_come_from_the_words_of_the_board_name(
    name: str, project: str, stem: str, title: str, fab_name: str
) -> None:
    """Split at hyphens, underscores, dots and spaces; keep capitals the user wrote."""
    assert scaffold.board_names(name) == Names(project, stem, title, fab_name)


def test_the_revision_goes_into_the_fab_name() -> None:
    """Use the template's revision, so a template at rev B makes rev B boards."""
    assert scaffold.board_names("my-board", "B").fab_name == "My_Board_revB"


@pytest.mark.parametrize("name", ["my-board", "x", "3d", "A.b_c d-e", "UPPER"])
def test_every_name_makes_a_stem_and_fab_name_that_pcbkit_toml_accepts(
    name: str,
) -> None:
    """Never produce a stem or fab name that load_config would refuse."""
    names = scaffold.board_names(name)
    assert NAME_PATTERN.fullmatch(names.stem)
    assert NAME_PATTERN.fullmatch(names.fab_name)


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("my+board", "'+'"),
        ("my/board", "'/'"),
        ("caf\u00e9", "'\u00e9'"),
        ("a:b;c", "':', ';'"),
    ],
)
def test_a_name_with_other_characters_is_refused_naming_them(
    name: str, bad: str
) -> None:
    """Say which characters are the problem, and what a name may use."""
    with pytest.raises(ProjectError) as caught:
        scaffold.board_names(name)
    assert bad in caught.value.message
    assert "letters, digits, hyphens and underscores" in caught.value.message


@pytest.mark.parametrize("name", ["", "---", "_.", " "])
def test_a_name_with_no_words_is_refused(name: str) -> None:
    """Refuse a name that has nothing left after the separators."""
    with pytest.raises(ProjectError, match="no letters or digits"):
        scaffold.board_names(name)


# --- the [board] table ----------------------------------------------------------------

TOML = """\
# stem = "from a comment"
[board]
stem = "old"            # KiCad file stem
title = "Old"           # shown in the title block
rev = "A"
fab_name = "Old_revA"   # prefix of the fab files

[other]
stem = "not the board's"
"""
NEW = {"stem": "my_board", "title": "My Board", "fab_name": "My_Board_revA"}


def test_rename_board_changes_the_values_and_nothing_else() -> None:
    """Replace each quoted value; spacing, comments and the other keys stay."""
    got = scaffold.rename_board(TOML, NEW)
    want = (
        TOML.replace('"old"', '"my_board"')
        .replace('"Old"', '"My Board"')
        .replace('"Old_revA"', '"My_Board_revA"')
    )
    assert got == want


def test_rename_board_leaves_the_other_tables_and_the_comments_alone() -> None:
    """Edit [board] only: a `stem` under another table, or in a comment, is not it."""
    got = scaffold.rename_board(TOML, {"stem": "new"})
    assert got.count('stem = "new"') == 1
    assert 'stem = "not the board\'s"' in got
    assert '# stem = "from a comment"' in got


def test_rename_board_writes_a_value_toml_can_read_back() -> None:
    """Escape what TOML needs escaped, so any value round-trips."""
    got = scaffold.rename_board(TOML, {"title": 'say "hi" \\ now'})
    assert tomllib.loads(got)["board"]["title"] == 'say "hi" \\ now'


def test_rename_board_refuses_a_table_that_lacks_a_key() -> None:
    """Fail loudly when the template's pcbkit.toml is not the shape this expects."""
    elsewhere = '[board]\nstem = "a"\n[other]\ntitle = "b"\n'
    with pytest.raises(ProjectError, match="title"):
        scaffold.rename_board(elsewhere, {"title": "x"})
    single = "[board]\ntitle = 'x'\n"
    with pytest.raises(ProjectError, match="title"):
        scaffold.rename_board(single, {"title": "y"})


# --- which files make a template ------------------------------------------------------

EXAMPLE_TREE = {
    "pcbkit.toml": "toml",
    "design.py": "design",
    "checks/test_a.py": "check",
    "golden/x.ses": "route",
    ".gitignore": "ignore",
    "docs/README.md": "a README below the top is kept",
    "docs/out/keep.txt": "so is an out/ below the top",
    "kicad/x.kicad_pcb": "generated",
    "out/fab/z.zip": "generated",
    "fab/a.csv": "generated",
    "archive/old/x.py": "the user's own",
    ".venv/bin/python": "environment",
    ".venv-kicad/bin/python": "environment",
    "pyproject.toml": "written by new",
    "README.md": "written by new",
    "uv.lock": "somebody's lock file",
    "__pycache__/design.cpython-39.pyc": "noise",
    "checks/__pycache__/test_a.cpython-39.pyc": "noise",
    "golden/.DS_Store": "noise",
    "layout.pyc": "noise",
    ".git/config": "noise",
}


def test_template_files_leave_out_what_commands_make_and_what_new_writes(
    tmp_path: Path,
) -> None:
    """Keep the project's own files, golden/ and dotfiles; drop the rest."""
    root = write_tree(tmp_path / "example", EXAMPLE_TREE)
    assert sorted(scaffold.template_files(root)) == [
        ".gitignore",
        "checks/test_a.py",
        "design.py",
        "docs/README.md",
        "docs/out/keep.txt",
        "golden/x.ses",
        "pcbkit.toml",
    ]
    assert scaffold.template_files(root)["golden/x.ses"] == b"route"


def test_template_differences_names_each_kind_of_difference(tmp_path: Path) -> None:
    """Report a missing file, an extra one and a changed one, each by name."""
    example = write_tree(tmp_path / "example", EXAMPLE_TREE)
    assert scaffold.template_differences(example, example) == []
    template = tmp_path / "template"
    shutil.copytree(example, template)
    assert scaffold.template_differences(example, template) == []
    (template / "design.py").write_text("edited", encoding="utf-8")
    (template / "golden" / "x.ses").unlink()
    (template / "extra.py").write_text("x", encoding="utf-8")
    (template / "kicad" / "made_by_a_command").write_text("x", encoding="utf-8")
    assert scaffold.template_differences(example, template) == [
        "missing from the template: golden/x.ses",
        "not in the example: extra.py",
        "differs: design.py",
    ]


def test_a_template_folder_that_is_missing_differs_in_every_file(
    tmp_path: Path,
) -> None:
    """Say every file is missing, rather than failing, for a template not made yet."""
    example = write_tree(tmp_path / "example", {"pcbkit.toml": "a", "design.py": "b"})
    assert scaffold.template_differences(example, tmp_path / "none") == [
        "missing from the template: design.py",
        "missing from the template: pcbkit.toml",
    ]


def test_sync_template_makes_the_template_equal_and_says_what_it_changed(
    tmp_path: Path,
) -> None:
    """Write what is missing or changed, remove what is stale, tidy empty folders."""
    example = write_tree(tmp_path / "example", EXAMPLE_TREE)
    template = write_tree(
        tmp_path / "template",
        {
            "design.py": "stale",
            "old.py": "gone from the example",
            "gone/deep/old.txt": "so is this, and its folders",
            "kicad/keep.txt": "generated: not the template's business",
        },
    )
    changes = scaffold.sync_template(example, template)
    assert "differs: design.py" in changes
    assert "not in the example: old.py" in changes
    assert "missing from the template: golden/x.ses" in changes
    assert scaffold.template_differences(example, template) == []
    assert not (template / "gone").exists()
    assert (template / "kicad" / "keep.txt").is_file()
    assert scaffold.sync_template(example, template) == []


def test_sync_template_makes_a_template_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    """Create the folder and everything in it."""
    example = write_tree(tmp_path / "example", EXAMPLE_TREE)
    template = tmp_path / "new" / "template"
    scaffold.sync_template(example, template)
    assert scaffold.template_differences(example, template) == []


# --- golden/ --------------------------------------------------------------------------

PCB = (
    '(footprint "R"\n\t(sheetfile "blinky.kicad_sch")\n)\n'
    '(footprint "D"\n\t(sheetfile "blinky.kicad_sch")\n)\n'
    '(net 1 "/blinky_2")\n(net 2 "/xblinky")\n(net 3 "/blinky-led")\n'
)


def golden(root: Path) -> Path:
    """Write a golden/ folder for a board with the stem blinky, and return it."""
    return write_tree(
        root / "golden",
        {
            "prerouted.kicad_pcb": PCB,
            "blinky.ses": "(session blinky\n  (base_design blinky)\n)\n",
            "blinky.dsn": "(pcb kicad/blinky.dsn\n  (parser)\n)\n",
        },
    )


def test_retarget_golden_renames_the_stem_files_and_the_stem_inside_them(
    tmp_path: Path,
) -> None:
    """Move the session and the DSN to the new stem, and rewrite the three files."""
    folder = golden(tmp_path)
    made = scaffold.retarget_golden(folder, "blinky", "my_board")
    assert made == ["my_board.dsn", "my_board.ses", "prerouted.kicad_pcb"]
    assert listing(folder) == made
    assert (folder / "my_board.ses").read_text(encoding="utf-8") == (
        "(session my_board\n  (base_design my_board)\n)\n"
    )
    assert (
        (folder / "my_board.dsn")
        .read_text(encoding="utf-8")
        .startswith("(pcb kicad/my_board.dsn\n")
    )
    board = (folder / "prerouted.kicad_pcb").read_text(encoding="utf-8")
    assert board.count('(sheetfile "my_board.kicad_sch")') == 2
    assert "blinky.kicad_sch" not in board


def test_retarget_golden_replaces_whole_words_only(tmp_path: Path) -> None:
    """Leave a net or a part that merely contains the old stem as it is."""
    folder = golden(tmp_path)
    scaffold.retarget_golden(folder, "blinky", "my_board")
    board = (folder / "prerouted.kicad_pcb").read_text(encoding="utf-8")
    for kept in ('"/blinky_2"', '"/xblinky"', '"/blinky-led"'):
        assert kept in board


def test_retarget_golden_with_the_same_stem_touches_nothing(tmp_path: Path) -> None:
    """Make no change when the new board has the template's own stem."""
    folder = golden(tmp_path)
    before = {name: (folder / name).read_bytes() for name in listing(folder)}
    assert scaffold.retarget_golden(folder, "blinky", "blinky") == sorted(before)
    assert {name: (folder / name).read_bytes() for name in listing(folder)} == before


def test_retarget_golden_without_a_folder_does_nothing(tmp_path: Path) -> None:
    """Accept a template that has no golden/."""
    assert scaffold.retarget_golden(tmp_path / "golden", "blinky", "x") == []


# --- the project's pyproject.toml -----------------------------------------------------

NAMES = scaffold.board_names("my-board")


def test_pyproject_depends_on_pcbkit_from_git_by_default() -> None:
    """Name pcbkit through its git repository, with pytest, and make no package."""
    data = tomllib.loads(scaffold.pyproject_text(NAMES))
    assert data["project"]["name"] == "my-board"
    assert data["project"]["requires-python"] == ">=3.9"
    assert data["project"]["dependencies"] == [
        "pcbkit @ git+https://github.com/bruk-io/pcbkit",
        "pytest>=8",
    ]
    assert data["tool"]["uv"] == {"package": False}


def test_pyproject_points_at_a_checkout_as_an_editable_path_source(
    tmp_path: Path,
) -> None:
    """Use the bare name as the dependency, and the folder as its source."""
    data = tomllib.loads(scaffold.pyproject_text(NAMES, tmp_path))
    assert data["project"]["dependencies"] == ["pcbkit", "pytest>=8"]
    assert data["tool"]["uv"] == {
        "package": False,
        "sources": {"pcbkit": {"path": str(tmp_path), "editable": True}},
    }


def test_pyproject_escapes_a_path_that_needs_it(tmp_path: Path) -> None:
    """Write a path with quotes and backslashes so that TOML reads the same path."""
    odd = tmp_path / 'say "hi"' / "back\\slash"
    data = tomllib.loads(scaffold.pyproject_text(NAMES, odd))
    assert data["tool"]["uv"]["sources"]["pcbkit"]["path"] == str(odd)


# --- the command: create_project ------------------------------------------------------


def test_new_makes_a_project_that_loads(here: Path) -> None:
    """Copy the template, name the board, and leave a folder pcbkit can open."""
    created = scaffold.create_project("my-board")
    root = here / "my-board"
    assert created.root == root.resolve()
    assert created.names == scaffold.board_names("my-board")
    assert created.template == "blinky"
    assert created.source == scaffold.PCBKIT_GIT
    board = load_project(root).config.board
    assert (board.stem, board.title, board.rev, board.fab_name) == (
        "my_board",
        "My Board",
        "A",
        "My_Board_revA",
    )
    assert list(created.files) == listing(root)


def test_new_writes_the_templates_files_plus_pyproject_and_readme(here: Path) -> None:
    """Hold exactly the template's files, golden/ renamed, and the two written ones."""
    created = scaffold.create_project("my-board")
    wanted = {n for n in scaffold.template_files(TEMPLATE) if "golden/" not in n}
    wanted |= {"pyproject.toml", "README.md"}
    wanted |= {f"golden/my_board.{ext}" for ext in ("ses", "dsn")}
    wanted |= {"golden/prerouted.kicad_pcb"}
    assert set(created.files) == wanted
    root = here / "my-board"
    assert not (root / "kicad").exists() and not (root / "out").exists()


def test_new_leaves_no_trace_of_the_template_name_in_golden(here: Path) -> None:
    """Rename the route files and what is inside them, so finalize finds its files."""
    scaffold.create_project("my-board")
    for path in (here / "my-board" / "golden").iterdir():
        text = path.read_text(encoding="utf-8")
        assert "blinky" not in text, path.name


def test_new_keeps_the_comments_of_the_template_toml(here: Path) -> None:
    """Change three values in pcbkit.toml and nothing else."""
    scaffold.create_project("my-board")
    new = (here / "my-board" / "pcbkit.toml").read_text(encoding="utf-8").splitlines()
    old = (TEMPLATE / "pcbkit.toml").read_text(encoding="utf-8").splitlines()
    assert len(new) == len(old)
    changed = [a.split("=")[0].strip() for a, b in zip(old, new) if a != b]
    assert changed == ["stem", "title", "fab_name"]
    assert "# KiCad file stem: kicad/<stem>.kicad_pcb" in "\n".join(new)


def test_new_writes_the_default_or_the_local_dependency(
    here: Path, tmp_path: Path
) -> None:
    """Use git unless a checkout is given, and then use that."""
    scaffold.create_project("one")
    git = tomllib.loads((here / "one" / "pyproject.toml").read_text("utf-8"))
    assert git["project"]["dependencies"][0].startswith("pcbkit @ git+https://")
    checkout = make_checkout(tmp_path / "checkout")
    created = scaffold.create_project("two", pcbkit_source=checkout)
    local = tomllib.loads((here / "two" / "pyproject.toml").read_text("utf-8"))
    assert local["tool"]["uv"]["sources"]["pcbkit"]["path"] == str(checkout.resolve())
    assert created.source == str(checkout.resolve())


def test_the_readme_names_the_board_and_commands_that_exist(here: Path) -> None:
    """Show the board's title and name, and only commands pcbkit has."""
    scaffold.create_project("my-board")
    readme = (here / "my-board" / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# My Board\n")
    assert "pcbkit new my-board" in readme
    commands = commands_in(readme)
    assert {
        "new",
        "setup",
        "build",
        "route",
        "promote",
        "finalize",
        "check",
    } <= commands
    assert commands <= set(cli.commands)


def test_new_refuses_a_folder_that_has_something_in_it(here: Path) -> None:
    """Never write into a folder with files in it, and leave it exactly as it was."""
    write_tree(here / "my-board", {"notes.txt": "mine"})
    with pytest.raises(ScaffoldError, match="already exists and is not empty"):
        scaffold.create_project("my-board")
    assert listing(here / "my-board") == ["notes.txt"]


def test_new_refuses_a_name_that_is_a_file(here: Path) -> None:
    """Treat a file called NAME like a folder with something in it."""
    (here / "my-board").write_text("a file", encoding="utf-8")
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold.create_project("my-board")
    assert (here / "my-board").read_text(encoding="utf-8") == "a file"


def test_new_uses_an_empty_folder(here: Path) -> None:
    """Fill a folder that exists and is empty."""
    (here / "my-board").mkdir()
    scaffold.create_project("my-board")
    assert (here / "my-board" / "pcbkit.toml").is_file()


def test_new_with_a_path_names_the_board_after_its_last_part(here: Path) -> None:
    """Make the parent folders, and name the board for the folder it is in."""
    created = scaffold.create_project("boards/2026/my-board")
    assert (here / "boards" / "2026" / "my-board" / "pcbkit.toml").is_file()
    assert created.names.stem == "my_board"


def test_new_with_a_dot_names_the_board_after_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fill the empty current folder, and call the board what the folder is called."""
    folder = tmp_path / "robot-arm"
    folder.mkdir()
    monkeypatch.chdir(folder)
    created = scaffold.create_project(".")
    assert created.names.title == "Robot Arm"
    assert (folder / "pcbkit.toml").is_file()


def test_new_with_an_unknown_template_names_the_choices(here: Path) -> None:
    """Say which templates there are."""
    with pytest.raises(
        ScaffoldError, match="no template called 'nope': choose from blinky"
    ):
        scaffold.create_project("my-board", "nope")
    assert listing(here) == []


def test_new_with_a_bad_name_makes_nothing(here: Path) -> None:
    """Refuse the name before creating any folder."""
    with pytest.raises(ProjectError):
        scaffold.create_project("my+board")
    assert listing(here) == [] and list(here.iterdir()) == []


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"pyproject.toml": '[project]\nname = "other"\n', "pcbkit/__init__.py": ""},
        {"pyproject.toml": '[project]\nname = "pcbkit"\n'},
        {"pcbkit/__init__.py": ""},
    ],
    ids=["empty", "another project", "no package", "no pyproject"],
)
def test_new_refuses_a_pcbkit_source_that_is_not_a_checkout(
    here: Path, tmp_path: Path, files: dict[str, str]
) -> None:
    """Stop before writing anything when --pcbkit-source is not a pcbkit checkout."""
    source = write_tree(tmp_path / "elsewhere", files)
    with pytest.raises(ScaffoldError, match="is not a pcbkit checkout"):
        scaffold.create_project("my-board", pcbkit_source=source)
    assert not (here / "my-board").exists()


def test_new_reports_a_missing_template_as_a_broken_install(
    here: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Say to reinstall pcbkit if the template did not ship."""
    monkeypatch.setattr(scaffold, "TEMPLATE_ROOT", tmp_path / "no-templates")
    with pytest.raises(ScaffoldError, match="reinstall pcbkit"):
        scaffold.create_project("my-board")
    assert not (here / "my-board").exists()


def test_a_failure_half_way_takes_out_what_was_written(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove a folder that new made, whatever step went wrong."""

    def fail(*args: object) -> list[str]:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(scaffold, "retarget_golden", fail)
    with pytest.raises(ScaffoldError, match="could not write .*No space left"):
        scaffold.create_project("my-board")
    assert not (here / "my-board").exists()


def test_a_failure_in_a_folder_that_was_there_empties_it_again(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave an empty folder that was empty, not a half-made project."""
    (here / "my-board").mkdir()

    def fail(*args: object) -> str:
        raise ProjectError("the template is not what this expects")

    monkeypatch.setattr(scaffold, "rename_board", fail)
    with pytest.raises(ProjectError, match="not what this expects"):
        scaffold.create_project("my-board")
    assert (here / "my-board").is_dir() and listing(here / "my-board") == []


def test_format_result_shows_the_folder_as_the_user_will_type_it(
    here: Path, tmp_path: Path
) -> None:
    """Print a relative folder from where the user is, an absolute one elsewhere."""
    created = scaffold.create_project("my-board")
    near = scaffold.format_result(created, cwd=here)
    assert "Made My Board in my-board/" in near and "  cd my-board\n" in near
    far = scaffold.format_result(created, cwd=tmp_path / "elsewhere")
    assert f"in {created.root}/" in far and f"  cd {created.root}\n" in far


# --- the command line -----------------------------------------------------------------


def test_the_command_says_what_it_made_and_what_to_type_next(here: Path) -> None:
    """Print the title, the template, the next commands, and exit 0."""
    result = invoke("new", "my-board")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("Made My Board in my-board/, from the blinky example (")
    assert "  cd my-board" in lines
    assert "  pcbkit setup" in lines
    assert (here / "my-board" / "pcbkit.toml").is_file()


def test_the_command_takes_pcbkit_from_an_option_or_the_environment(
    here: Path, tmp_path: Path
) -> None:
    """Honour --pcbkit-source, and PCBKIT_SOURCE when the option is not given."""
    checkout = make_checkout(tmp_path / "checkout")
    by_option = invoke("new", "one", "--pcbkit-source", str(checkout))
    assert by_option.exit_code == 0, by_option.output
    assert f"pcbkit comes from {checkout.resolve()}." in by_option.output
    by_env = invoke("new", "two", env={"PCBKIT_SOURCE": str(checkout)})
    assert by_env.exit_code == 0, by_env.output
    for name in ("one", "two"):
        text = (here / name / "pyproject.toml").read_text(encoding="utf-8")
        assert f'path = "{checkout.resolve()}"' in text
    assert scaffold.PCBKIT_SOURCE_ENV == "PCBKIT_SOURCE"


def test_the_option_beats_the_environment(here: Path, tmp_path: Path) -> None:
    """Use the folder named on the command line, not the one in the variable."""
    first = make_checkout(tmp_path / "first")
    second = make_checkout(tmp_path / "second")
    result = invoke(
        "new",
        "my-board",
        "--pcbkit-source",
        str(first),
        env={"PCBKIT_SOURCE": str(second)},
    )
    assert result.exit_code == 0, result.output
    assert str(first.resolve()) in (here / "my-board" / "pyproject.toml").read_text(
        "utf-8"
    )


def test_the_command_rejects_an_unknown_template_as_a_usage_error(here: Path) -> None:
    """Exit 2, naming --from, and make nothing."""
    result = invoke("new", "my-board", "--from", "nope")
    assert result.exit_code == 2
    assert "--from" in result.output and "choose from blinky" in result.output
    assert list(here.iterdir()) == []


@pytest.mark.parametrize(
    ("argv", "code", "text"),
    [
        (["new", "my+board"], 1, "Error: 'my+board' has '+' in it"),
        (["new", "full"], 1, "already exists and is not empty"),
        (["new", "x", "--pcbkit-source", "missing-folder"], 2, "does not exist"),
        (["new", "x", "--pcbkit-source", "full"], 1, "is not a pcbkit checkout"),
    ],
)
def test_the_command_turns_each_failure_into_a_message(
    here: Path, argv: list[str], code: int, text: str
) -> None:
    """Print one error line, with the exit code click gives that kind of mistake."""
    write_tree(here / "full", {"file": "x"})
    result = invoke(*argv)
    assert result.exit_code == code
    assert text in result.output
    assert "Traceback" not in result.output


def test_the_help_shows_the_options_and_the_environment_variable() -> None:
    """Document --from, --pcbkit-source and PCBKIT_SOURCE where the user looks."""
    output = invoke("new", "--help").output
    for text in (
        "--from",
        "--pcbkit-source",
        "PCBKIT_SOURCE",
        "pcbkit setup",
        "blinky",
    ):
        assert text in output, text


# --- the real template and the real example -------------------------------------------


def test_the_template_is_a_copy_of_the_example() -> None:
    """Fail when the example or the template was changed without the other."""
    differences = scaffold.template_differences(EXAMPLE, TEMPLATE)
    assert not differences, (
        "pcbkit/templates/board is not a copy of examples/blinky:\n  "
        + "\n  ".join(differences)
        + f"\nrun `{FIX}` to bring the template up to date"
    )


@pytest.mark.parametrize("side", ["example", "template"])
def test_the_drift_guard_catches_one_changed_file_on_either_side(
    tmp_path: Path, side: str
) -> None:
    """Change one byte of one file in a copy of either folder, and see it reported."""
    example = materialise(scaffold.template_files(EXAMPLE), tmp_path / "example")
    template = materialise(scaffold.template_files(TEMPLATE), tmp_path / "template")
    assert scaffold.template_differences(example, template) == []
    target = (example if side == "example" else template) / "specs.py"
    target.write_text(target.read_text(encoding="utf-8") + "# one more line\n", "utf-8")
    assert scaffold.template_differences(example, template) == ["differs: specs.py"]


def test_the_drift_guard_catches_a_file_added_to_or_taken_from_one_side(
    tmp_path: Path,
) -> None:
    """Report a file only the example has, and one only the template has."""
    example = materialise(scaffold.template_files(EXAMPLE), tmp_path / "example")
    template = materialise(scaffold.template_files(TEMPLATE), tmp_path / "template")
    (example / "extra.py").write_text("x = 1\n", encoding="utf-8")
    (template / "golden" / "prerouted.kicad_pcb").unlink()
    assert scaffold.template_differences(example, template) == [
        "missing from the template: extra.py",
        "missing from the template: golden/prerouted.kicad_pcb",
    ]


def test_the_template_holds_every_file_a_project_needs() -> None:
    """Carry the modules, a check, and a golden route named for the template's stem."""
    stem = load_config(TEMPLATE / "pcbkit.toml").board.stem
    files = set(scaffold.template_files(TEMPLATE))
    needed = {
        "pcbkit.toml",
        "design.py",
        "layout.py",
        "routing.py",
        "silk.py",
        "specs.py",
        "mutants.py",
        ".gitignore",
        "golden/prerouted.kicad_pcb",
        f"golden/{stem}.ses",
        f"golden/{stem}.dsn",
    }
    assert needed <= files
    assert any(n.startswith("checks/test_") for n in files)


def test_the_template_has_none_of_what_new_writes_or_commands_make() -> None:
    """Keep pyproject.toml, README.md and generated folders out of the template."""
    on_disk = [n for n in listing(TEMPLATE) if not n.endswith(".DS_Store")]
    assert not [n for n in on_disk if n in scaffold.NOT_COPIED]
    for generated in (*scaffold.GENERATED_DIRS, ".venv", "__pycache__"):
        assert not [n for n in on_disk if generated in Path(n).parts], generated
    assert on_disk == sorted(scaffold.template_files(TEMPLATE))


def test_the_template_toml_is_a_valid_config_that_new_can_rename() -> None:
    """Parse it, and find all three [board] values that `new` replaces."""
    text = (TEMPLATE / "pcbkit.toml").read_text(encoding="utf-8")
    renamed = scaffold.rename_board(text, NEW)
    board = tomllib.loads(renamed)["board"]
    assert (board["stem"], board["title"], board["fab_name"]) == (
        "my_board",
        "My Board",
        "My_Board_revA",
    )
    assert load_config(TEMPLATE / "pcbkit.toml").board.rev == "A"


def test_the_templates_python_files_are_valid_python() -> None:
    """Compile each module, so a broken edit to the example cannot ship."""
    for name in scaffold.template_files(TEMPLATE):
        if name.endswith(".py"):
            source = (TEMPLATE / name).read_text(encoding="utf-8")
            compile(source, name, "exec")


def test_the_template_carries_no_machine_paths() -> None:
    """Keep the golden route and everything else free of where it was made."""
    for name, data in scaffold.template_files(TEMPLATE).items():
        text = data.decode("utf-8", errors="replace")
        for local in ("/Users/", "/private/", "/home/", "/tmp/", "C:\\"):
            assert local not in text, f"{name} mentions {local}"


# --- the sync script ------------------------------------------------------------------


def load_sync_script() -> ModuleType:
    """Import examples/sync_template.py by path."""
    spec = importlib.util.spec_from_file_location("sync_template", SYNC_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sync_script_checks_and_fixes_a_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a drifted template with --check, and make it equal without."""
    script = load_sync_script()
    copy = materialise(scaffold.template_files(TEMPLATE), tmp_path / "board")
    monkeypatch.setattr(scaffold, "TEMPLATE_ROOT", tmp_path)
    runner = CliRunner()

    ok = runner.invoke(script.main, ["--check"])
    assert ok.exit_code == 0, ok.output
    assert "up to date with examples/blinky" in ok.output

    (copy / "layout.py").write_text("W, H = 1.0, 1.0\n", encoding="utf-8")
    drifted = runner.invoke(script.main, ["--check"])
    assert drifted.exit_code == 1
    assert "differs: layout.py" in drifted.output
    assert FIX in drifted.output
    assert (copy / "layout.py").read_text(encoding="utf-8") == "W, H = 1.0, 1.0\n"

    fixed = runner.invoke(script.main, [])
    assert fixed.exit_code == 0, fixed.output
    assert "differs: layout.py" in fixed.output
    assert scaffold.template_differences(EXAMPLE, copy) == []


# --- the docs -------------------------------------------------------------------------


def docs_text() -> str:
    """Return the new/setup section of the interface doc."""
    text = DOCS.read_text(encoding="utf-8")
    start = text.index("## Starting a board: pcbkit new and pcbkit setup")
    return text[start:]


def test_the_docs_table_of_names_is_what_the_code_makes() -> None:
    """Show the names board_names makes, row by row."""
    section = docs_text()
    for name in ("my-board", "ESP32-carrier", "blinky2"):
        made = scaffold.board_names(name)
        row = (
            f"| `{name}` | `{made.stem}` | `{made.title}` | `{made.fab_name}` "
            f"| `{made.project}` |"
        )
        assert row in section, row


def test_the_docs_give_the_dependencies_the_code_writes() -> None:
    """Show the git dependency, the path source and the options by name."""
    section = docs_text()
    assert f'"pcbkit @ {scaffold.PCBKIT_GIT}"' in section
    assert '"pytest>=8"' in section
    assert "[tool.uv.sources]" in section and "editable = true" in section
    for text in ("--from", "--pcbkit-source", scaffold.PCBKIT_SOURCE_ENV):
        assert text in section, text


def test_the_docs_describe_what_setup_does_and_the_jar_it_fetches() -> None:
    """Name each step, the jar's URL and size, and where it is kept."""
    section = docs_text()
    assert bootstrap.FREEROUTING_URL in section
    assert str(bootstrap.FREEROUTING_BYTES) in section
    for text in (
        "~/.local/share/pcbkit/freerouting-1.9.0.jar",
        "~/.local/share/freerouting/freerouting-1.9.0.jar",
        "FREEROUTING_JAR",
        "uv venv --python",
        "--system-site-packages",
        "uv sync --python",
        ".python-version",
    ):
        assert text in section, text


def test_the_docs_say_how_the_template_is_kept_in_step() -> None:
    """Name the sync script and the files the template leaves out."""
    section = docs_text()
    assert FIX in section
    for text in ("examples/blinky", "pcbkit/templates/board", "--check"):
        assert text in section, text


def test_the_example_readme_runs_commands_that_exist() -> None:
    """Show only pcbkit commands the CLI has."""
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    commands = commands_in(readme)
    assert {
        "new",
        "setup",
        "build",
        "route",
        "promote",
        "finalize",
        "check",
    } <= commands
    assert "mutants" in commands
    assert commands <= set(cli.commands)
