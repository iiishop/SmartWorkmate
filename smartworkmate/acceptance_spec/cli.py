from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .ir import compile_to_ir
from .parser import parse_spec
from .pytest_codegen import generate_pytest_module
from .pytest_runner import run_pytest
from .reporting import (
    CheckPlan,
    StatementPlan,
    build_verdict_from_pytest,
    render_lvf,
    write_verdict_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="smartworkmate-acceptance",
        description="Compile ASL acceptance spec and generate verdict artifacts",
    )
    parser.add_argument(
        "task_md_path",
        nargs="?",
        type=Path,
        help="Task markdown path (default mode)",
    )
    parser.add_argument("--asl-file", type=Path, help="Path to raw ASL spec file")
    parser.add_argument(
        "--task-md",
        type=Path,
        help="Task markdown path (same as positional argument)",
    )
    parser.add_argument("--task-id", default="")
    parser.add_argument("--spec-id", default="SPEC-ACCEPTANCE")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs") / "acceptances",
        help="Directory for final acceptance reports",
    )
    parser.add_argument(
        "--include-json",
        action="store_true",
        help="Also write verbose verdict.json (default output is compact verdict.lvf)",
    )
    args = parser.parse_args()

    run_id = args.run_id or _default_run_id()
    source_kind, source_path = _resolve_source_paths(
        positional_task_md=args.task_md_path,
        task_md=args.task_md,
        asl_file=args.asl_file,
    )
    asl_source = _load_asl_source(source_kind=source_kind, source_path=source_path)
    task_id = args.task_id or _derive_task_id(
        source_kind=source_kind, source_path=source_path
    )
    base_name = _derive_output_base_name(
        source_kind=source_kind, source_path=source_path
    )

    spec = parse_spec(asl_source)
    ir = compile_to_ir(spec)
    generated_code = generate_pytest_module(ir)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="smartworkmate_acceptance_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        generated_test_path = tmp_root / "test_asl_generated.py"
        generated_test_path.write_text(generated_code, encoding="utf-8")
        junit_xml = tmp_root / "pytest-junit.xml"

        run_result = run_pytest(
            generated_test_path,
            workdir=Path.cwd(),
            timeout_seconds=600,
            junit_xml=junit_xml,
        )

        verdict = build_verdict_from_pytest(
            task_id=task_id,
            run_id=run_id,
            spec_id=args.spec_id,
            statement_count=len(ir.statements),
            statement_plans=_build_statement_plans(ir),
            run_result=run_result,
            artifacts=[],
        )

    paths = write_verdict_files(
        verdict,
        output_dir=output_dir,
        include_json=args.include_json,
        base_name=base_name,
    )

    print(render_lvf(verdict))
    print(f"saved_lvf={paths['lvf']}")
    if "json" in paths:
        print(f"saved_json={paths['json']}")

    print(f"status={verdict.status}")

    return 0 if verdict.status == "pass" else 1


def _build_statement_plans(ir) -> list[StatementPlan]:
    plans: list[StatementPlan] = []
    for statement in ir.statements:
        checks: list[CheckPlan] = []
        for idx, value in enumerate(statement.value_checks, start=1):
            checks.append(
                CheckPlan(
                    key=f"value_{idx}",
                    kind="value",
                    label=f"{value.left_code} {value.op} {value.right_code}",
                )
            )
        for idx, perf in enumerate(statement.perf_checks, start=1):
            if perf.warmup_count:
                perf_args = f"{perf.sample_count}, {perf.warmup_count}"
            else:
                perf_args = f"{perf.sample_count}"
            checks.append(
                CheckPlan(
                    key=f"perf_{idx}",
                    kind="perf",
                    label=f"{perf.metric}({perf_args}) {perf.op} {perf.threshold}",
                )
            )
        plans.append(StatementPlan(checks=checks))
    return plans


def _default_run_id() -> str:
    now = datetime.now(timezone.utc)
    return "run_" + now.strftime("%Y%m%d_%H%M%S")


def _resolve_source_paths(
    *,
    positional_task_md: Path | None,
    task_md: Path | None,
    asl_file: Path | None,
) -> tuple[str, Path]:
    candidates: list[tuple[str, Path]] = []
    if positional_task_md is not None:
        candidates.append(("task_md", positional_task_md))
    if task_md is not None:
        candidates.append(("task_md", task_md))
    if asl_file is not None:
        candidates.append(("asl_file", asl_file))

    if not candidates:
        raise ValueError("Provide a task markdown path or --asl-file")
    unique = {(kind, str(path)) for kind, path in candidates}
    if len(unique) > 1:
        raise ValueError("Use only one source input: task path or --asl-file")
    return candidates[0]


def _load_asl_source(*, source_kind: str, source_path: Path) -> str:
    text = source_path.read_text(encoding="utf-8")
    if source_kind == "asl_file":
        return text
    return _extract_acceptance_asl_from_task_md(text)


def _derive_task_id(*, source_kind: str, source_path: Path) -> str:
    if source_kind == "task_md":
        return source_path.stem
    return "TASK-UNKNOWN"


def _derive_output_base_name(*, source_kind: str, source_path: Path) -> str:
    if source_kind == "task_md":
        return source_path.stem
    return source_path.stem


def _extract_acceptance_asl_from_task_md(content: str) -> str:
    lines = content.splitlines()
    section_start: int | None = None
    for idx, line in enumerate(lines):
        if re.match(r"^##\s*交付验收\s*$", line.strip()):
            section_start = idx + 1
            break
    if section_start is None:
        raise ValueError("Cannot find '## 交付验收' section in task markdown")

    section_lines: list[str] = []
    for line in lines[section_start:]:
        if re.match(r"^##\s+", line):
            break
        section_lines.append(line)

    section = "\n".join(section_lines).strip()
    fence_match = re.search(r"```(?:asl)?\n([\s\S]*?)\n```", section)
    if fence_match:
        return fence_match.group(1).strip()
    return section


if __name__ == "__main__":
    raise SystemExit(main())
