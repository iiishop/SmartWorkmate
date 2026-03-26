from __future__ import annotations

import pytest

from smartworkmate.acceptance_spec.parser import ParseError, parse_spec


def test_parser_supports_case_insensitive_keywords() -> None:
    source = """
    UsInG python;
    TeSt algo.sort_non_decreasing as sort;
    GiVeN {
      A_small: int[] = [3, 1, 2, 2];
    }
    ExPeCt {
      sort(A_small) == [1, 2, 2, 3] and $p_ms(100, 20) <= 8;
    }
    """

    spec = parse_spec(source)

    assert spec.language == "python"
    assert spec.tests[0].alias == "sort"
    assert len(spec.expect) == 1
    assert len(spec.expect[0].predicates) == 2


def test_parser_requires_semicolon_per_expect_statement() -> None:
    source = """
    using python;
    test algo.sort_non_decreasing as sort;
    given {
      A_small: int[] = [3, 1, 2, 2];
    }
    expect {
      sort(A_small) == [1, 2, 2, 3]
    }
    """

    with pytest.raises(ParseError):
        parse_spec(source)
