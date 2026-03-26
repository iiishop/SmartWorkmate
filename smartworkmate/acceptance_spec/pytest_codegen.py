from __future__ import annotations

from smartworkmate.acceptance_spec.ir import IRSpec


OP_MAP = {
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}


def generate_pytest_module(ir: IRSpec) -> str:
    if ir.language.lower() != "python":
        raise ValueError(
            f"Only python is supported in pytest adapter, got {ir.language!r}"
        )

    lines: list[str] = []
    required_helpers = _required_builtin_helpers(ir)
    lines.extend(
        [
            "import importlib",
            "import math",
            "import statistics",
            "import time",
            "",
            "",
            "def _resolve(path: str):",
            "    parts = path.split('.')",
            "    if len(parts) < 2:",
            "        raise ValueError(f'Invalid target path: {path}')",
            "    module = None",
            "    attr_parts = []",
            "    for split_index in range(len(parts), 0, -1):",
            "        module_name = '.'.join(parts[:split_index])",
            "        try:",
            "            module = importlib.import_module(module_name)",
            "            attr_parts = parts[split_index:]",
            "            break",
            "        except Exception:",
            "            continue",
            "    if module is None:",
            "        raise ValueError(f'Cannot import module from target path: {path}')",
            "    current = module",
            "    for attr_name in attr_parts:",
            "        current = getattr(current, attr_name)",
            "    return current",
            "",
            "",
            "def _p95(values):",
            "    ordered = sorted(values)",
            "    index = max(0, math.ceil(0.95 * len(ordered)) - 1)",
            "    return ordered[index]",
            "",
            "",
            "def _cmp(lhs, op, rhs):",
            "    if op == '==':",
            "        return lhs == rhs",
            "    if op == '!=':",
            "        return lhs != rhs",
            "    if op == '<':",
            "        return lhs < rhs",
            "    if op == '<=':",
            "        return lhs <= rhs",
            "    if op == '>':",
            "        return lhs > rhs",
            "    if op == '>=':",
            "        return lhs >= rhs",
            "    raise ValueError(f'Unknown operator: {op}')",
            "",
            "",
            "def _measure(func, sample_n, warmup_n, metric):",
            "    runtime_errors = []",
            "    for _ in range(warmup_n):",
            "        try:",
            "            func()",
            "        except Exception as exc:",
            "            runtime_errors.append(repr(exc))",
            "            return 0.0, runtime_errors",
            "",
            "    values = []",
            "    for _ in range(sample_n):",
            "        start = time.perf_counter()",
            "        try:",
            "            func()",
            "        except Exception as exc:",
            "            runtime_errors.append(repr(exc))",
            "            return 0.0, runtime_errors",
            "        end = time.perf_counter()",
            "        values.append((end - start) * 1000.0)",
            "",
            "    if metric == '$p_ms':",
            "        return statistics.fmean(values), runtime_errors",
            "    if metric == '$p95_ms':",
            "        return _p95(values), runtime_errors",
            "    raise ValueError(f'Unknown perf metric: {metric}')",
            "",
            "",
        ]
    )

    lines.extend(_helper_source_lines(required_helpers))

    for alias, path in ir.tests.items():
        lines.append(f"{alias} = _resolve({path!r})")
    lines.append("")

    for index, statement in enumerate(ir.statements, start=1):
        lines.append(f"def test_statement_{index}():")
        if (
            not ir.given_values
            and not statement.value_checks
            and not statement.perf_checks
        ):
            lines.append("    pass")
            lines.append("")
            continue

        for name in statement.used_given_names:
            value_code = ir.given_values[name]
            lines.append(f"    {name} = {value_code}")
        lines.append("    failures = []")

        for pred_index, check in enumerate(statement.value_checks, start=1):
            op = OP_MAP[check.op]
            lines.append(f"    lhs_{pred_index} = {check.left_code}")
            lines.append(f"    rhs_{pred_index} = {check.right_code}")
            lines.append(
                f"    if not _cmp(lhs_{pred_index}, {op!r}, rhs_{pred_index}):"
            )
            lines.append(
                "        failures.append("
                f"'value check {pred_index} failed: '"
                f" + repr(lhs_{pred_index}) + ' {op} ' + repr(rhs_{pred_index})"
                ")"
            )

        if statement.perf_checks:
            for perf_index, perf_check in enumerate(statement.perf_checks, start=1):
                lines.append(f"    metric_{perf_index} = {perf_check.metric!r}")
                lines.append(f"    sample_n_{perf_index} = {perf_check.sample_count}")
                lines.append(f"    warmup_n_{perf_index} = {perf_check.warmup_count}")
                lines.append(
                    f"    measured_{perf_index}, runtime_errors_{perf_index} = _measure("
                    f"lambda: {statement.bound_call_code}, sample_n_{perf_index}, warmup_n_{perf_index}, metric_{perf_index})"
                )
                lines.append(f"    if runtime_errors_{perf_index}:")
                lines.append(
                    f"        failures.append('performance runtime errors ({perf_index}): ' + '; '.join(runtime_errors_{perf_index}))"
                )
                lines.append(
                    f"    elif not _cmp(measured_{perf_index}, {perf_check.op!r}, {perf_check.threshold}):"
                )
                lines.append(
                    "        failures.append("
                    f"'perf check {perf_index} failed: '"
                    f" + metric_{perf_index} + ' measured=' + str(measured_{perf_index})"
                    f" + ' expected {perf_check.op} {perf_check.threshold}'"
                    ")"
                )

        lines.append("    assert not failures, '\\n'.join(failures)")
        lines.append("")

    return "\n".join(lines)


def _required_builtin_helpers(ir: IRSpec) -> set[str]:
    required: set[str] = set()
    for statement in ir.statements:
        for check in statement.value_checks:
            combined = check.left_code + "\n" + check.right_code
            if "__asl_builtin_multiset(" in combined:
                required.add("multiset")
            if "__asl_builtin_approx_eq(" in combined:
                required.add("approx_eq")
            if "__asl_builtin_contains(" in combined:
                required.add("contains")
            if "__asl_builtin_len(" in combined:
                required.add("len")
    return required


def _helper_source_lines(required: set[str]) -> list[str]:
    lines: list[str] = []
    if "multiset" in required:
        lines.extend(
            [
                "from collections import Counter",
                "",
                "",
                "def __asl_builtin_multiset(value):",
                "    return tuple(sorted(Counter(value).items()))",
                "",
                "",
            ]
        )
    if "approx_eq" in required:
        lines.extend(
            [
                "def __asl_builtin_approx_eq(lhs, rhs, eps=1e-9):",
                "    return abs(lhs - rhs) <= eps",
                "",
                "",
            ]
        )
    if "contains" in required:
        lines.extend(
            [
                "def __asl_builtin_contains(container, item):",
                "    return item in container",
                "",
                "",
            ]
        )
    if "len" in required:
        lines.extend(
            [
                "def __asl_builtin_len(value):",
                "    return len(value)",
                "",
                "",
            ]
        )
    return lines
