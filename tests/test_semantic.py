from __future__ import annotations

import pytest

from smartworkmate.acceptance_spec.parser import parse_spec
from smartworkmate.acceptance_spec.semantic import (
    SemanticValidationError,
    validate_semantics,
)


def test_semantic_rejects_perf_binding_with_multiple_call_contexts() -> None:
    source = """
    using python;
    test algo.f1 as f1;
    test algo.f2 as f2;
    given {
      A: int[] = [1, 2];
      B: int[] = [3, 4];
    }
    expect {
      f1(A) == 10 and f2(B) == 20 and $p_ms(100, 20) <= 8;
    }
    """

    spec = parse_spec(source)
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantics(spec)

    codes = {item.code for item in exc_info.value.errors}
    assert "PERF_BINDING_ERROR" in codes


def test_semantic_accepts_perf_when_each_statement_has_single_call_context() -> None:
    source = """
    using python;
    test algo.f1 as f1;
    test algo.f2 as f2;
    given {
      A: int[] = [1, 2];
      B: int[] = [3, 4];
    }
    expect {
      f1(A) == 10 and $p_ms(100, 20) <= 8;
      f2(B) == 20 and $p95_ms(200, 20) <= 30;
    }
    """

    spec = parse_spec(source)
    validate_semantics(spec)


def test_semantic_rejects_invalid_perf_argument_values() -> None:
    source = """
    using python;
    test algo.f1 as f1;
    given {
      A: int[] = [1, 2];
    }
    expect {
      f1(A) == 10 and $p_ms(0, -1) <= 8;
    }
    """

    spec = parse_spec(source)
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantics(spec)

    codes = {item.code for item in exc_info.value.errors}
    assert "PERF_ARG_TYPE" in codes


def test_semantic_rejects_alias_name_colliding_with_builtin_family() -> None:
    source = """
    using python;
    test algo.metrics as p_ms;
    given {
      A: int[] = [1, 2];
    }
    expect {
      p_ms(A) == 10;
    }
    """

    spec = parse_spec(source)
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_semantics(spec)

    codes = {item.code for item in exc_info.value.errors}
    assert "RESERVED_TEST_ALIAS" in codes
