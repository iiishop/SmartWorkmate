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
    assert "def _measure(func, sample_n, warmup_n, metric):" in code
    assert "_measure(lambda: sort(A_small), sample_n_1, warmup_n_1, metric_1)" in code
    assert "_measure(lambda: sort(A_small), sample_n_2, warmup_n_2, metric_2)" in code


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


def test_codegen_injects_only_required_given_variables() -> None:
    source = """
    using python;
    test algo.sort_non_decreasing as sort;
    test algo.max_value as maxv;
    given {
      A_small: int[] = [3, 1, 2, 2];
      B_small: int[] = [5, 1, 9, 2];
    }
    expect {
      sort(A_small) == [1, 2, 2, 3];
      maxv(B_small) == 9;
    }
    """

    ir = compile_to_ir(parse_spec(source))
    code = generate_pytest_module(ir)

    statement_1 = code.split("def test_statement_1():", 1)[1].split(
        "def test_statement_2():", 1
    )[0]
    statement_2 = code.split("def test_statement_2():", 1)[1]

    assert "A_small = [3, 1, 2, 2]" in statement_1
    assert "B_small = [5, 1, 9, 2]" not in statement_1
    assert "B_small = [5, 1, 9, 2]" in statement_2
    assert "A_small = [3, 1, 2, 2]" not in statement_2


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
    assert "__asl_builtin_len" not in code
