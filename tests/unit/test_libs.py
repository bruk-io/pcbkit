"""Unit tests for pcbkit.libs: the project's own symbol and footprint libraries."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from pcbkit import libs
from pcbkit.kicad.sexp import dump, find, findall, parse, q
from pcbkit.libs import ProjectLibs, write_project_libs
from pcbkit.project import Project, ProjectError, load_project
from tests.board_files import TOML, restored_imports, write_file


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """Keep footprints.py modules and sys.path entries from outliving a test."""
    with restored_imports():
        yield


FOOTPRINTS_PY = """\
from pcbkit.kicad.sexp import q

LIB = "mylib"


def symbols():
    return [
        ["symbol", q("Thing"), ["property", q("Reference"), q("U")]],
        ["symbol", q("Other"), ["property", q("Reference"), q("J")]],
    ]


def footprints():
    return {
        "Pad2": ["footprint", q("Pad2"), ["layer", q("F.Cu")], ["attr", "smd"]],
        "Hole": ["footprint", q("Hole"), ["layer", q("F.Cu")]],
    }
"""

VENDORED = '(footprint "Vendored"\n\t(layer "F.Cu")\n\t(version 20241229)\n)\n'


def make_project(root: Path, footprints_py: str | None = None) -> Project:
    """Write a pcbkit.toml (and footprints.py if given) into ``root`` and load it."""
    write_file(root / "pcbkit.toml", TOML)
    if footprints_py is not None:
        write_file(root / "footprints.py", footprints_py)
    return load_project(root)


# --- the writers ------------------------------------------------------------------


def test_a_symbol_library_has_the_header_and_the_symbols(tmp_path: Path) -> None:
    """Write a kicad_symbol_lib that parses back to what was given."""
    nodes = [["symbol", q("A"), ["in_bom", "yes"]], ["symbol", q("B")]]
    path = tmp_path / "x.kicad_sym"
    libs.write_symbol_lib(path, nodes)
    text = path.read_text(encoding="utf-8")
    assert text.endswith(")\n")
    lib = parse(text)
    assert lib[0] == "kicad_symbol_lib"
    assert find(lib, "version") == ["version", "20241209"]
    assert find(lib, "generator") == ["generator", "pcbkit"]
    assert [s[1] for s in findall(lib, "symbol")] == ["A", "B"]


def test_each_footprint_is_written_to_its_own_kicad_mod(tmp_path: Path) -> None:
    """Name each file after the footprint and keep its content."""
    made = {
        "One": ["footprint", q("One"), ["layer", q("F.Cu")]],
        "Two": ["footprint", q("Two"), ["attr", "smd"]],
    }
    written = libs.write_footprint_lib(tmp_path / "lib.pretty", made)
    assert [p.name for p in written] == ["One.kicad_mod", "Two.kicad_mod"]
    assert (tmp_path / "lib.pretty" / "One.kicad_mod").read_text() == dump(
        made["One"]
    ) + "\n"


def test_vendored_footprints_are_copied_byte_for_byte(tmp_path: Path) -> None:
    """Copy .kicad_mod files unchanged, and nothing else in the folder."""
    source = tmp_path / "footprints"
    write_file(source / "Vendored.kicad_mod", VENDORED)
    write_file(source / "README.md", "provenance notes")
    write_file(source / "model.step", "not a footprint")
    files = libs.vendored_files(tmp_path)
    assert [p.name for p in files] == ["Vendored.kicad_mod"]
    copies = libs.copy_vendored(files, tmp_path / "out.pretty")
    assert [p.name for p in copies] == ["Vendored.kicad_mod"]
    assert copies[0].read_bytes() == (source / "Vendored.kicad_mod").read_bytes()
    assert sorted(p.name for p in (tmp_path / "out.pretty").iterdir()) == [
        "Vendored.kicad_mod"
    ]


def test_no_footprints_folder_means_nothing_to_copy(tmp_path: Path) -> None:
    """Give an empty list rather than fail."""
    assert libs.vendored_files(tmp_path) == []


def test_a_library_table_names_the_library_through_kiprjmod() -> None:
    """Point the table at the library next to the project file."""
    assert libs.lib_table("sym", "mylib", "mylib.kicad_sym", "My Board: symbols") == (
        "(sym_lib_table\n"
        "  (version 7)\n"
        '  (lib (name "mylib")(type "KiCad")(uri "${KIPRJMOD}/mylib.kicad_sym")'
        '(options "")(descr "My Board: symbols"))\n'
        ")\n"
    )
    assert libs.lib_table("fp", "mylib", "mylib.pretty", "x").startswith(
        "(fp_lib_table"
    )


def test_a_table_with_no_library_is_empty_but_valid() -> None:
    """List nothing when the project has no library of that kind."""
    assert (
        libs.lib_table("fp", "mylib", None, "x") == "(fp_lib_table\n  (version 7)\n)\n"
    )


def test_a_quote_in_the_description_is_escaped() -> None:
    """Keep a board title with quotes from breaking the table."""
    table = libs.lib_table("sym", "m", "m.kicad_sym", 'The "Best" board \\ ever')
    assert '(descr "The \\"Best\\" board \\\\ ever")' in table
    assert parse(table)  # still valid S-expression text


def test_the_project_file_is_seeded_once_and_never_overwritten(tmp_path: Path) -> None:
    """Write a minimal project file, then leave a saved one alone."""
    path = libs.ensure_project_file(tmp_path, "my_board")
    assert path == tmp_path / "my_board.kicad_pro"
    assert json.loads(path.read_text()) == {
        "meta": {"filename": "my_board.kicad_pro", "version": 3}
    }
    path.write_text('{"board": {"saved": "by pcbnew"}}\n')
    assert libs.ensure_project_file(tmp_path, "my_board") == path
    assert path.read_text() == '{"board": {"saved": "by pcbnew"}}\n'


# --- write_project_libs -----------------------------------------------------------


def test_a_project_with_no_libraries_gets_empty_tables_and_a_project_file(
    tmp_path: Path,
) -> None:
    """Write no library, but leave KiCad a project file and tables that list nothing."""
    result = write_project_libs(make_project(tmp_path))
    kicad = tmp_path / "kicad"
    assert result == ProjectLibs(nickname="my_board")
    assert result.symbol_libs == {}
    assert sorted(p.name for p in kicad.iterdir()) == [
        "fp-lib-table",
        "my_board.kicad_pro",
        "sym-lib-table",
    ]
    assert (kicad / "sym-lib-table").read_text() == "(sym_lib_table\n  (version 7)\n)\n"


def test_the_hooks_make_the_libraries_and_the_tables(tmp_path: Path) -> None:
    """Write mylib.kicad_sym, mylib.pretty and tables that list them."""
    result = write_project_libs(make_project(tmp_path, FOOTPRINTS_PY))
    kicad = tmp_path / "kicad"
    assert result.nickname == "mylib"
    assert result.symbol_file == kicad / "mylib.kicad_sym"
    assert result.footprint_dir == kicad / "mylib.pretty"
    assert result.symbols == ("Thing", "Other")
    assert result.footprints == ("Hole", "Pad2")
    assert result.symbol_libs == {"mylib": kicad / "mylib.kicad_sym"}
    assert [
        s[1] for s in findall(parse((kicad / "mylib.kicad_sym").read_text()), "symbol")
    ] == [
        "Thing",
        "Other",
    ]
    assert sorted(p.name for p in (kicad / "mylib.pretty").iterdir()) == [
        "Hole.kicad_mod",
        "Pad2.kicad_mod",
    ]
    assert (
        '(uri "${KIPRJMOD}/mylib.kicad_sym")' in (kicad / "sym-lib-table").read_text()
    )
    assert '(uri "${KIPRJMOD}/mylib.pretty")' in (kicad / "fp-lib-table").read_text()
    assert '(name "mylib")' in (kicad / "fp-lib-table").read_text()


def test_the_library_is_named_after_the_stem_when_there_is_no_lib(
    tmp_path: Path,
) -> None:
    """Default LIB to the board's stem."""
    source = FOOTPRINTS_PY.replace('LIB = "mylib"\n', "")
    result = write_project_libs(make_project(tmp_path, source))
    assert result.nickname == "my_board"
    assert (tmp_path / "kicad" / "my_board.kicad_sym").is_file()
    assert (tmp_path / "kicad" / "my_board.pretty").is_dir()


def test_footprints_py_may_define_only_what_it_needs(tmp_path: Path) -> None:
    """Skip a library whose hook is absent, and list nothing for it."""
    source = FOOTPRINTS_PY[: FOOTPRINTS_PY.index("def symbols")] + (
        'def footprints():\n    return {"Pad2": ["footprint", q("Pad2")]}\n'
    )
    result = write_project_libs(make_project(tmp_path, source))
    assert result.symbol_file is None
    assert result.footprint_dir is not None
    assert (tmp_path / "kicad" / "sym-lib-table").read_text().count("(lib ") == 0
    assert (tmp_path / "kicad" / "fp-lib-table").read_text().count("(lib ") == 1
    assert not list((tmp_path / "kicad").glob("*.kicad_sym"))


def test_vendored_files_alone_make_a_footprint_library(tmp_path: Path) -> None:
    """Build mylib.pretty from footprints/ when there is no footprints.py."""
    write_file(tmp_path / "footprints" / "Vendored.kicad_mod", VENDORED)
    write_file(tmp_path / "footprints" / "README.md", "notes")
    result = write_project_libs(make_project(tmp_path))
    assert result.nickname == "my_board"
    assert result.footprints == ("Vendored",)
    pretty = tmp_path / "kicad" / "my_board.pretty"
    assert [p.name for p in pretty.iterdir()] == ["Vendored.kicad_mod"]


def test_generated_and_vendored_footprints_share_one_library(tmp_path: Path) -> None:
    """Put both kinds in the same .pretty folder."""
    write_file(tmp_path / "footprints" / "Vendored.kicad_mod", VENDORED)
    result = write_project_libs(make_project(tmp_path, FOOTPRINTS_PY))
    assert result.footprints == ("Hole", "Pad2", "Vendored")
    pretty = tmp_path / "kicad" / "mylib.pretty"
    assert sorted(p.name for p in pretty.iterdir()) == [
        "Hole.kicad_mod",
        "Pad2.kicad_mod",
        "Vendored.kicad_mod",
    ]


def test_a_footprint_that_is_both_generated_and_vendored_is_an_error(
    tmp_path: Path,
) -> None:
    """Refuse to let one silently overwrite the other."""
    write_file(tmp_path / "footprints" / "Pad2.kicad_mod", VENDORED)
    with pytest.raises(ProjectError, match="Pad2.*same name"):
        write_project_libs(make_project(tmp_path, FOOTPRINTS_PY))


def test_a_rebuild_replaces_generated_files_and_keeps_the_project_file(
    tmp_path: Path,
) -> None:
    """Rewrite the libraries each time, but leave a saved project file alone."""
    proj = make_project(tmp_path, FOOTPRINTS_PY)
    write_project_libs(proj)
    kicad = tmp_path / "kicad"
    (kicad / "mylib.pretty" / "Pad2.kicad_mod").write_text("stale")
    (kicad / "my_board.kicad_pro").write_text('{"saved": true}\n')
    write_project_libs(proj)
    assert (
        (kicad / "mylib.pretty" / "Pad2.kicad_mod").read_text().startswith("(footprint")
    )
    assert (kicad / "my_board.kicad_pro").read_text() == '{"saved": true}\n'


def test_a_library_the_project_dropped_leaves_the_tables(tmp_path: Path) -> None:
    """Empty the tables once the hooks are gone, so KiCad finds no old files."""
    proj = make_project(tmp_path, FOOTPRINTS_PY)
    write_project_libs(proj)
    (tmp_path / "footprints.py").unlink()
    with restored_imports():
        write_project_libs(proj)
    assert (tmp_path / "kicad" / "fp-lib-table").read_text().count("(lib ") == 0
    assert (tmp_path / "kicad" / "sym-lib-table").read_text().count("(lib ") == 0


@pytest.mark.parametrize(
    ("hooks", "expected"),
    [
        ("def symbols():\n    return 'Thing'\n", "symbols() should return a list"),
        (
            "def symbols():\n    return [['pin', 'x']]\n",
            'nodes like ["symbol", q("Name")',
        ),
        ("def symbols():\n    return ['text']\n", 'nodes like ["symbol"'),
        (
            "def symbols():\n    return [['symbol', 'A'], ['symbol', 'A']]\n",
            "makes 'A' twice",
        ),
        ("def footprints():\n    return ['Pad']\n", "should return a dict"),
        (
            "def footprints():\n    return {'Pad': ['footprint', 'Other']}\n",
            "node whose name matches its key",
        ),
        (
            "def footprints():\n    return {'Pad': ['symbol', 'Pad']}\n",
            "node whose name matches its key",
        ),
        (
            "def footprints():\n    return {'a/b': ['footprint', 'a/b']}\n",
            "not usable as a footprint file name",
        ),
        ("LIB = 'a:b'\n", "LIB should be a library name"),
        ("LIB = 5\n", "LIB should be a library name"),
        ("LIB = ''\n", "LIB should be a library name"),
    ],
)
def test_a_bad_hook_is_an_error_that_says_what_is_wrong(
    tmp_path: Path, hooks: str, expected: str
) -> None:
    """Name the hook and what it should have returned."""
    with pytest.raises(ProjectError, match="footprints.py") as err:
        write_project_libs(make_project(tmp_path, hooks))
    assert expected in err.value.message
