from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from .ir import compile_to_ir
from .parser import parse_spec
from .pytest_codegen import generate_pytest_module
from .pytest_runner import run_pytest
from .reporting import build_verdict_from_pytest, write_verdict_files


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="smartworkmate-acceptance",
        description="Compile ASL acceptance spec and generate verdict artifacts",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--asl-file", type=Path, help="Path to raw ASL spec file")
    source_group.add_argument(
        "--task-md",
        type=Path,
        help="Task markdown file path (extracts ASL from the 交付验收 section)",
    )
    parser.add_argument("--task-id", default="TASK-UNKNOWN")
    parser.add_argument("--spec-id", default="SPEC-ACCEPTANCE")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts") / "acceptance"
    )
    parser.add_argument(
        "--generated-test",
        type=Path,
        default=Path("artifacts")
        / "acceptance"
        / "generated_tests"
        / "test_asl_generated.py",
        help="Path for generated pytest module",
    )
    parser.add_argument(
        "--include-json",
        action="store_true",
        help="Also write verbose verdict.json (default output is compact verdict.lvf)",
    )
    args = parser.parse_args()

    run_id = args.run_id or _default_run_id()
    asl_source = _load_asl_source(args.asl_file, args.task_md)

    spec = parse_spec(asl_source)
    ir = compile_to_ir(spec)
    generated_code = generate_pytest_module(ir)

    generated_test_path = args.generated_test
    generated_test_path.parent.mkdir(parents=True, exist_ok=True)
    generated_test_path.write_text(generated_code, encoding="utf-8")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    junit_xml = output_dir / "pytest-junit.xml"

    run_result = run_pytest(
        generated_test_path,
        workdir=Path.cwd(),
        timeout_seconds=600,
        junit_xml=junit_xml,
    )

    stdout_path = output_dir / "pytest_stdout.log"
    stderr_path = output_dir / "pytest_stderr.log"
    stdout_path.write_text(run_result.stdout, encoding="utf-8")
    stderr_path.write_text(run_result.stderr, encoding="utf-8")

    verdict = build_verdict_from_pytest(
        task_id=args.task_id,
        run_id=run_id,
        spec_id=args.spec_id,
        statement_count=len(ir.statements),
        run_result=run_result,
        artifacts=[
            str(generated_test_path),
            str(junit_xml),
            str(stdout_path),
            str(stderr_path),
        ],
    )
    paths = write_verdict_files(
        verdict,
        output_dir=output_dir,
        include_json=args.include_json,
    )

    print(f"status={verdict.status}")
    print(f"verdict_lvf={paths['lvf']}")
    if "json" in paths:
        print(f"verdict_json={paths['json']}")

    return 0 if verdict.status == "pass" else 1


def _default_run_id() -> str:
    now = datetime.now(timezone.utc)
    return "run_" + now.strftime("%Y%m%d_%H%M%S")


def _load_asl_source(asl_file: Path | None, task_md: Path | None) -> str:
    if asl_file is not None:
        return asl_file.read_text(encoding="utf-8")
    assert task_md is not None
    text = task_md.read_text(encoding="utf-8")
    return _extract_acceptance_asl_from_task_md(text)


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
