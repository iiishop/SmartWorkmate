from __future__ import annotations

from smartworkmate.acceptance_spec.ir import compile_to_ir
from smartworkmate.acceptance_spec.parser import parse_spec
from smartworkmate.acceptance_spec.pytest_codegen import generate_pytest_module


def test_codegen_emits_non_short_circuit_failure_collection() -> None:
    source = """
    using python;
    test algo.sort_non_decreasing as sort;
    given {
      A_small: int[] = [3, 1, 2, 2];
    }
    expect {
      sort(A_small) == [1, 2, 2, 3] and $p_ms(100, 20) <= 8 and $p95_ms(200, 20) <= 100;
    }
    """

    ir = compile_to_ir(parse_spec(source))
    code = generate_pytest_module(ir)

    assert "failures = []" in code
    assert "assert not failures" in code
    assert "for _ in range(warmup_n_1):" in code
    assert "for _ in range(sample_n_1):" in code
    assert "for _ in range(warmup_n_2):" in code
    assert "for _ in range(sample_n_2):" in code


def test_codegen_binds_perf_measurement_to_statement_call_context() -> None:
    source = """
    using python;
    test algo.f1 as f1;
    given {
      A: int[] = [1, 2];
    }
    expect {
      f1(A) == 10 and $p_ms(5, 1) <= 2;
    }
    """

    ir = compile_to_ir(parse_spec(source))
    code = generate_pytest_module(ir)

    assert "f1(A)" in code
    assert "metric_1 = '$p_ms'" in code


def test_codegen_supports_builtin_value_functions() -> None:
    source = """
    using python;
    test algo.sort_non_decreasing as sort;
    given {
      A: int[] = [3, 1, 2, 2];
    }
    expect {
      $multiset(sort(A)) == $multiset([1, 2, 2, 3]);
    }
    """

    ir = compile_to_ir(parse_spec(source))
    code = generate_pytest_module(ir)

    assert "__asl_builtin_multiset" in code
