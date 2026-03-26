from __future__ import annotations

from dataclasses import dataclass

from .builtins import BUILTINS
from .ast import (
    CallExpr,
    Comparison,
    ExpectStatement,
    Expression,
    SpecDocument,
)


@dataclass(slots=True)
class SemanticError:
    code: str
    message: str


class SemanticValidationError(ValueError):
    def __init__(self, errors: list[SemanticError]) -> None:
        self.errors = errors
        super().__init__("; ".join(error.message for error in errors))


def validate_semantics(spec: SpecDocument) -> None:
    errors: list[SemanticError] = []
    alias_map = {binding.alias: binding.target_path for binding in spec.tests}
    reserved_aliases = BUILTINS.reserved_aliases()

    if len(alias_map) != len(spec.tests):
        errors.append(
            SemanticError(
                code="DUPLICATE_TEST_ALIAS",
                message="Duplicate aliases in test bindings",
            )
        )

    for alias in alias_map:
        if alias.lower() in reserved_aliases:
            errors.append(
                SemanticError(
                    code="RESERVED_TEST_ALIAS",
                    message=f"Alias {alias!r} conflicts with reserved builtin function names",
                )
            )

    given_names = [item.name for item in spec.given]
    if len(set(given_names)) != len(given_names):
        errors.append(
            SemanticError(
                code="DUPLICATE_GIVEN_VAR",
                message="Duplicate variables in given block",
            )
        )

    for idx, statement in enumerate(spec.expect, start=1):
        errors.extend(_validate_statement(statement, idx, alias_map))

    if errors:
        raise SemanticValidationError(errors)


def _validate_statement(
    statement: ExpectStatement,
    statement_index: int,
    alias_map: dict[str, str],
) -> list[SemanticError]:
    errors: list[SemanticError] = []

    perf_comparisons = [
        item
        for item in statement.predicates
        if isinstance(item.left, CallExpr)
        and BUILTINS.is_category(item.left.name, "perf")
    ]

    if perf_comparisons:
        bound_calls = _collect_non_perf_calls(statement)
        if len(bound_calls) != 1:
            errors.append(
                SemanticError(
                    code="PERF_BINDING_ERROR",
                    message=(
                        f"Statement {statement_index} must contain exactly one non-builtin call "
                        "when using perf builtins"
                    ),
                )
            )

    for comparison in statement.predicates:
        errors.extend(_validate_perf_comparison(comparison, statement_index))
        errors.extend(_validate_call_aliases(comparison, alias_map, statement_index))

    return errors


def _validate_perf_comparison(
    comparison: Comparison,
    statement_index: int,
) -> list[SemanticError]:
    errors: list[SemanticError] = []
    if not isinstance(comparison.left, CallExpr):
        return errors
    spec = BUILTINS.get(comparison.left.name)
    if spec is None or spec.category != "perf":
        return errors

    argc = len(comparison.left.args)
    if argc not in spec.arg_counts:
        errors.append(
            SemanticError(
                code="PERF_ARG_COUNT",
                message=(
                    f"Statement {statement_index} perf call {comparison.left.name} requires "
                    f"{', '.join(str(item) for item in spec.arg_counts)} argument(s)"
                ),
            )
        )
        return errors

    for pos, arg in enumerate(comparison.left.args, start=1):
        if not _is_positive_int_literal(arg) and not (
            pos == 2 and _is_non_negative_int_literal(arg)
        ):
            errors.append(
                SemanticError(
                    code="PERF_ARG_TYPE",
                    message=(
                        f"Statement {statement_index} perf argument {pos} must be "
                        "an integer (n>=1, warmup>=0)"
                    ),
                )
            )

    return errors


def _validate_call_aliases(
    comparison: Comparison,
    alias_map: dict[str, str],
    statement_index: int,
) -> list[SemanticError]:
    errors: list[SemanticError] = []
    for call in _walk_calls(comparison.left):
        if BUILTINS.is_builtin(call.name):
            continue
        if call.name not in alias_map:
            errors.append(
                SemanticError(
                    code="UNKNOWN_CALL_ALIAS",
                    message=f"Statement {statement_index} references unknown alias {call.name!r}",
                )
            )
    for call in _walk_calls(comparison.right):
        if BUILTINS.is_builtin(call.name):
            continue
        if call.name not in alias_map:
            errors.append(
                SemanticError(
                    code="UNKNOWN_CALL_ALIAS",
                    message=f"Statement {statement_index} references unknown alias {call.name!r}",
                )
            )
    return errors


def _collect_non_perf_calls(statement: ExpectStatement) -> list[CallExpr]:
    calls: list[CallExpr] = []
    for comparison in statement.predicates:
        calls.extend(
            call
            for call in _walk_calls(comparison.left)
            if not BUILTINS.is_builtin(call.name)
        )
        calls.extend(
            call
            for call in _walk_calls(comparison.right)
            if not BUILTINS.is_builtin(call.name)
        )
    unique: dict[tuple[str, str], CallExpr] = {}
    for call in calls:
        key = (call.name, repr(call.args))
        unique[key] = call
    return list(unique.values())


def _walk_calls(expr: Expression) -> list[CallExpr]:
    if isinstance(expr, CallExpr):
        result = [expr]
        for arg in expr.args:
            result.extend(_walk_calls(arg))
        return result
    return []


def _is_positive_int_literal(expr: Expression) -> bool:
    from .ast import NumberLiteral

    return (
        isinstance(expr, NumberLiteral)
        and isinstance(expr.value, int)
        and expr.value >= 1
    )


def _is_non_negative_int_literal(expr: Expression) -> bool:
    from .ast import NumberLiteral

    return (
        isinstance(expr, NumberLiteral)
        and isinstance(expr.value, int)
        and expr.value >= 0
    )
