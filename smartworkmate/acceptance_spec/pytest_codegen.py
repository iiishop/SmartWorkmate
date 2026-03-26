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
    lines.extend(
        [
            "import importlib",
            "from collections import Counter",
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
            "def __asl_builtin_multiset(value):",
            "    return tuple(sorted(Counter(value).items()))",
            "",
            "",
            "def __asl_builtin_approx_eq(lhs, rhs, eps=1e-9):",
            "    return abs(lhs - rhs) <= eps",
            "",
            "",
            "def __asl_builtin_contains(container, item):",
            "    return item in container",
            "",
            "",
            "def __asl_builtin_len(value):",
            "    return len(value)",
            "",
            "",
        ]
    )

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

        for name, value_code in ir.given_values.items():
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
            lines.append("    samples = {}")
            lines.append("    runtime_errors = []")
            for perf_index, perf_check in enumerate(statement.perf_checks, start=1):
                lines.append(f"    metric_{perf_index} = {perf_check.metric!r}")
                lines.append(f"    sample_n_{perf_index} = {perf_check.sample_count}")
                lines.append(f"    warmup_n_{perf_index} = {perf_check.warmup_count}")
                lines.append("    values = []")
                lines.append(f"    for _ in range(warmup_n_{perf_index}):")
                lines.append("        try:")
                lines.append(f"            {statement.bound_call_code}")
                lines.append("        except Exception as exc:")
                lines.append("            runtime_errors.append(repr(exc))")
                lines.append("            break")
                lines.append("    if not runtime_errors:")
                lines.append(f"        for _ in range(sample_n_{perf_index}):")
                lines.append("            start = time.perf_counter()")
                lines.append("            try:")
                lines.append(f"                {statement.bound_call_code}")
                lines.append("            except Exception as exc:")
                lines.append("                runtime_errors.append(repr(exc))")
                lines.append("                break")
                lines.append("            end = time.perf_counter()")
                lines.append("            values.append((end - start) * 1000.0)")
                lines.append(f"    samples[{perf_index}] = values")

                lines.append("    if runtime_errors:")
                lines.append(
                    "        failures.append('performance runtime errors: ' + '; '.join(runtime_errors))"
                )
                lines.append("    else:")
                lines.append(f"        if metric_{perf_index} == '$p_ms':")
                lines.append(
                    f"            measured_{perf_index} = statistics.fmean(samples[{perf_index}])"
                )
                lines.append("        else:")
                lines.append(
                    f"            measured_{perf_index} = _p95(samples[{perf_index}])"
                )
                lines.append(
                    f"        if not _cmp(measured_{perf_index}, {perf_check.op!r}, {perf_check.threshold}):"
                )
                lines.append(
                    "            failures.append("
                    f"'perf check {perf_index} failed: '"
                    f" + metric_{perf_index} + ' measured=' + str(measured_{perf_index})"
                    f" + ' expected {perf_check.op} {perf_check.threshold}'"
                    ")"
                )

        lines.append("    assert not failures, '\\n'.join(failures)")
        lines.append("")

    return "\n".join(lines)
