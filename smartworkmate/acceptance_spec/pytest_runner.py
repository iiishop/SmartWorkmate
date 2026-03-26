from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class PytestRunResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    junit_xml: Path | None = None


def run_pytest(
    test_file: Path,
    *,
    workdir: Path,
    timeout_seconds: int = 300,
    junit_xml: Path | None = None,
) -> PytestRunResult:
    command = [sys.executable, "-m", "pytest", str(test_file), "-q"]
    if junit_xml is not None:
        command.extend(["--junitxml", str(junit_xml)])
    completed = subprocess.run(
        command,
        cwd=workdir,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
    )
    return PytestRunResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
        junit_xml=junit_xml,
    )
