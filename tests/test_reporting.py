from __future__ import annotations

from pathlib import Path

from smartworkmate.acceptance_spec.reporting import (
    build_verdict_from_pytest,
    render_lvf,
    write_verdict_files,
)
from smartworkmate.acceptance_spec.pytest_runner import PytestRunResult


def test_reporting_defaults_to_lvf_output(tmp_path: Path) -> None:
    junit_xml = tmp_path / "junit.xml"
    junit_xml.write_text(
        """
<testsuite tests="2" failures="1" errors="0">
  <testcase classname="x" name="test_statement_1" />
  <testcase classname="x" name="test_statement_2">
    <failure message="assert 1 == 2">trace</failure>
  </testcase>
</testsuite>
""".strip(),
        encoding="utf-8",
    )

    run_result = PytestRunResult(
        exit_code=1,
        stdout="",
        stderr="",
        command=["pytest"],
        junit_xml=junit_xml,
    )
    verdict = build_verdict_from_pytest(
        task_id="TSK-1",
        run_id="run-1",
        spec_id="SPEC-1",
        statement_count=2,
        run_result=run_result,
        artifacts=["artifacts/test.py"],
    )

    output_paths = write_verdict_files(verdict, output_dir=tmp_path)
    assert "lvf" in output_paths
    assert "json" not in output_paths
    assert output_paths["lvf"].name == "verdict.lvf"

    text = render_lvf(verdict)
    assert "status=fail" in text
    assert "s1 pass" in text
    assert "s2 fail" in text


def test_reporting_optionally_writes_json(tmp_path: Path) -> None:
    junit_xml = tmp_path / "junit.xml"
    junit_xml.write_text(
        """
<testsuite tests="1" failures="0" errors="0">
  <testcase classname="x" name="test_statement_1" />
</testsuite>
""".strip(),
        encoding="utf-8",
    )

    verdict = build_verdict_from_pytest(
        task_id="TSK-2",
        run_id="run-2",
        spec_id="SPEC-2",
        statement_count=1,
        run_result=PytestRunResult(
            exit_code=0,
            stdout="",
            stderr="",
            command=["pytest"],
            junit_xml=junit_xml,
        ),
    )
    output_paths = write_verdict_files(verdict, output_dir=tmp_path, include_json=True)
    assert "lvf" in output_paths
    assert "json" in output_paths
