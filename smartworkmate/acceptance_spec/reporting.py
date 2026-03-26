from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .pytest_runner import PytestRunResult
from .verdict_schema import SpecResult, StatementResult, UnifiedVerdict


def build_verdict_from_pytest(
    *,
    task_id: str,
    run_id: str,
    spec_id: str,
    statement_count: int,
    run_result: PytestRunResult,
    artifacts: list[str] | None = None,
) -> UnifiedVerdict:
    statement_results = _statement_results_from_junit(
        statement_count=statement_count,
        junit_xml=run_result.junit_xml,
    )

    if not statement_results:
        statement_results = [
            StatementResult(
                statement_index=idx,
                status="error",
                detail="No statement result was captured",
            )
            for idx in range(1, statement_count + 1)
        ]

    spec_status = _aggregate_status([item.status for item in statement_results])
    summary = _build_summary(statement_results)
    return UnifiedVerdict(
        task_id=task_id,
        run_id=run_id,
        status=spec_status,
        summary=summary,
        spec_results=[
            SpecResult(
                spec_id=spec_id,
                status=spec_status,
                statement_results=statement_results,
            )
        ],
        artifacts=list(artifacts or []),
    )


def render_lvf(verdict: UnifiedVerdict) -> str:
    lines = [
        "VERDICT v1",
        f"task={verdict.task_id} run={verdict.run_id} status={verdict.status}",
        f"summary={verdict.summary}",
    ]
    for spec in verdict.spec_results:
        lines.append(
            f"spec={spec.spec_id} status={spec.status} count={len(spec.statement_results)}"
        )
        for statement in spec.statement_results:
            metric_text = ""
            if statement.metrics:
                metric_text = " | " + ", ".join(
                    f"{key}={value}" for key, value in sorted(statement.metrics.items())
                )
            lines.append(
                f"s{statement.statement_index} {statement.status} | {statement.detail}{metric_text}"
            )
    if verdict.artifacts:
        lines.append("artifacts: " + ", ".join(verdict.artifacts))
    return "\n".join(lines)


def write_verdict_files(
    verdict: UnifiedVerdict,
    *,
    output_dir: Path,
    include_json: bool = False,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    lvf_path = output_dir / "verdict.lvf"
    lvf_path.write_text(render_lvf(verdict), encoding="utf-8")
    paths["lvf"] = lvf_path

    if include_json:
        json_path = output_dir / "verdict.json"
        json_path.write_text(
            json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["json"] = json_path

    return paths


def _statement_results_from_junit(
    *,
    statement_count: int,
    junit_xml: Path | None,
) -> list[StatementResult]:
    if junit_xml is None or not junit_xml.exists():
        return []

    tree = ET.parse(junit_xml)
    root = tree.getroot()
    by_index: dict[int, StatementResult] = {}

    for testcase in root.iter("testcase"):
        name = testcase.get("name", "")
        index = _extract_statement_index(name)
        if index is None:
            continue

        failure = testcase.find("failure")
        error = testcase.find("error")
        if failure is not None:
            by_index[index] = StatementResult(
                statement_index=index,
                status="fail",
                detail=(
                    failure.get("message") or failure.text or "statement failed"
                ).strip(),
            )
            continue
        if error is not None:
            by_index[index] = StatementResult(
                statement_index=index,
                status="error",
                detail=(
                    error.get("message") or error.text or "statement errored"
                ).strip(),
            )
            continue
        by_index[index] = StatementResult(
            statement_index=index,
            status="pass",
            detail="statement passed",
        )

    results: list[StatementResult] = []
    for idx in range(1, statement_count + 1):
        results.append(
            by_index.get(
                idx,
                StatementResult(
                    statement_index=idx,
                    status="error",
                    detail="statement missing from junit report",
                ),
            )
        )
    return results


def _extract_statement_index(name: str) -> int | None:
    prefix = "test_statement_"
    if not name.startswith(prefix):
        return None
    tail = name[len(prefix) :]
    if not tail.isdigit():
        return None
    return int(tail)


def _aggregate_status(statuses: list[str]) -> str:
    if any(item == "error" for item in statuses):
        return "error"
    if any(item == "fail" for item in statuses):
        return "fail"
    return "pass"


def _build_summary(statement_results: list[StatementResult]) -> str:
    passed = sum(1 for item in statement_results if item.status == "pass")
    failed = sum(1 for item in statement_results if item.status == "fail")
    errored = sum(1 for item in statement_results if item.status == "error")
    return (
        f"{len(statement_results)} statements: "
        f"{passed} passed, {failed} failed, {errored} errored"
    )
