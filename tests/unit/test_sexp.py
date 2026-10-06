"""Unit tests for the KiCad S-expression parser and serialiser."""

from __future__ import annotations

import pytest

from pcbkit.kicad.sexp import QStr, dump, find, findall, parse, q, walk

SAMPLE = """(kicad_sch
\t(version 20250114)
\t(generator "pcbkit")
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 10.16 20.32 0)
\t\t(property "Reference" "R1"
\t\t\t(at 0 0 0)
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 1.27 0)
\t\t)
\t)
)"""


def test_round_trip_is_byte_identical() -> None:
    assert dump(parse(SAMPLE)) == SAMPLE


def test_quoted_strings_keep_their_quotes_and_bare_atoms_do_not() -> None:
    tree = parse('(a "b c" d)')
    assert tree == ["a", "b c", "d"]
    assert isinstance(tree[1], QStr) and not isinstance(tree[2], QStr)
    assert dump(tree) == '(a "b c" d)'


def test_escaped_quotes_and_backslashes_round_trip() -> None:
    text = r'(text "say \"hi\" \\ there")'
    tree = parse(text)
    assert tree[1] == 'say "hi" \\ there'
    assert dump(tree) == text


def test_floats_bools_and_negative_zero_serialise_like_kicad() -> None:
    assert dump(["at", 1.5, 2.0, -0.0]) == "(at 1.5 2 0)"
    assert dump(["at", 0.123456]) == "(at 0.1235)"
    assert dump(["hide", True]) == "(hide yes)"
    assert dump(["hide", False]) == "(hide no)"


def test_q_marks_a_string_for_quoting() -> None:
    assert dump(["name", q("J1")]) == '(name "J1")'


def test_short_flat_lists_stay_on_one_line_and_long_ones_wrap() -> None:
    assert "\n" not in dump(["xy", 1, 2])
    long = ["pts"] + [["xy", i, i] for i in range(3)]
    assert dump(long).count("\n") == 4


def test_find_findall_and_walk() -> None:
    tree = parse(SAMPLE)
    sym = find(tree, "symbol")
    assert sym is not None and find(sym, "lib_id") == ["lib_id", "Device:R"]
    assert [p[1] for p in findall(sym, "property")] == ["Reference", "Value"]
    assert find(tree, "nope") is None
    heads = [n[0] for n in walk(tree)]
    assert heads.count("property") == 2 and heads[0] == "kicad_sch"


def test_several_top_level_lists_come_back_as_a_list() -> None:
    assert parse("(a)(b)") == [["a"], ["b"]]


def test_garbage_raises_a_parse_error_with_its_position() -> None:
    with pytest.raises(ValueError, match="parse error at"):
        parse('(a "unterminated')
