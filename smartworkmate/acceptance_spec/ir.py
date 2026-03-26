from __future__ import annotations

from dataclasses import dataclass

from .builtins import BUILTINS
from .ast import (
    ArrayLiteral,
    BoolLiteral,
    CallExpr,
    Comparison,
    ExpectStatement,
    Identifier,
    NumberLiteral,
    SpecDocument,
    StringLiteral,
)
from .semantic import validate_semantics


@dataclass(slots=True)
class IRPerfCheck:
    metric: str
    sample_count: int
    warmup_count: int
    op: str
    threshold: float


@dataclass(slots=True)
class IRValueCheck:
    left_code: str
    op: str
    right_code: str


@dataclass(slots=True)
class IRStatement:
    bound_call_code: str | None
    value_checks: list[IRValueCheck]
    perf_checks: list[IRPerfCheck]


@dataclass(slots=True)
class IRSpec:
    language: str
    tests: dict[str, str]
    given_values: dict[str, str]
    statements: list[IRStatement]


def compile_to_ir(spec: SpecDocument) -> IRSpec:
    validate_semantics(spec)
    tests = {item.alias: item.target_path for item in spec.tests}
    given_values = {item.name: expr_to_python(item.value) for item in spec.given}
    statements = [_compile_statement(stmt, tests) for stmt in spec.expect]
    return IRSpec(
        language=spec.language,
        tests=tests,
        given_values=given_values,
        statements=statements,
    )


def _compile_statement(
    statement: ExpectStatement, tests: dict[str, str]
) -> IRStatement:
    value_checks: list[IRValueCheck] = []
    perf_checks: list[IRPerfCheck] = []
    bound_call: str | None = None

    for predicate in statement.predicates:
        left = predicate.left
        if isinstance(left, CallExpr) and BUILTINS.is_category(left.name, "perf"):
            perf_checks.append(_compile_perf_check(predicate))
            continue
        value_checks.append(
            IRValueCheck(
                left_code=expr_to_python(predicate.left),
                op=predicate.op,
                right_code=expr_to_python(predicate.right),
            )
        )

    if perf_checks:
        bound_call = _resolve_bound_call(statement, tests)

    return IRStatement(
        bound_call_code=bound_call, value_checks=value_checks, perf_checks=perf_checks
    )


def _resolve_bound_call(statement: ExpectStatement, tests: dict[str, str]) -> str:
    call_expr: CallExpr | None = None
    for predicate in statement.predicates:
        for expr in (predicate.left, predicate.right):
            candidate = _find_first_non_perf_call(expr)
            if candidate is None:
                continue
            call_expr = candidate
            break
        if call_expr is not None:
            break
    if call_expr is None:
        raise ValueError("Expected a bound function call for performance checks")
    if call_expr.name not in tests:
        raise ValueError(f"Unknown test alias {call_expr.name!r}")
    return expr_to_python(call_expr)


def _find_first_non_perf_call(expr):
    if isinstance(expr, CallExpr):
        if not BUILTINS.is_builtin(expr.name):
            return expr
        for arg in expr.args:
            nested = _find_first_non_perf_call(arg)
            if nested is not None:
                return nested
    return None


def _compile_perf_check(predicate: Comparison) -> IRPerfCheck:
    assert isinstance(predicate.left, CallExpr)
    args = predicate.left.args
    n = _require_int(args[0])
    warmup = _require_int(args[1]) if len(args) == 2 else 0
    threshold = _require_number(predicate.right)
    return IRPerfCheck(
        metric=predicate.left.name,
        sample_count=n,
        warmup_count=warmup,
        op=predicate.op,
        threshold=threshold,
    )


def expr_to_python(expr) -> str:
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, NumberLiteral):
        return repr(expr.value)
    if isinstance(expr, StringLiteral):
        return repr(expr.value)
    if isinstance(expr, BoolLiteral):
        return "True" if expr.value else "False"
    if isinstance(expr, ArrayLiteral):
        items = ", ".join(expr_to_python(item) for item in expr.items)
        return f"[{items}]"
    if isinstance(expr, CallExpr):
        args = ", ".join(expr_to_python(arg) for arg in expr.args)
        name = expr.name
        if BUILTINS.is_builtin(name):
            name = _builtin_to_python_name(name)
        return f"{name}({args})"
    raise TypeError(f"Unsupported expression: {expr!r}")


def _builtin_to_python_name(name: str) -> str:
    return "__asl_builtin_" + name.removeprefix("$").replace(".", "_")


def _require_int(expr) -> int:
    if not isinstance(expr, NumberLiteral) or not isinstance(expr.value, int):
        raise TypeError("Expected integer literal")
    return expr.value


def _require_number(expr) -> float:
    if not isinstance(expr, NumberLiteral):
        raise TypeError("Expected numeric literal")
    return float(expr.value)
