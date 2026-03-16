from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from smartworkmate.proactive import (
    FINDING_SCAN_EXCLUDES,
    _detect_repo_base_branch,
    _git_code_findings,
)


class ProactiveFindingsTests(unittest.TestCase):
    def test_git_code_findings_excludes_auto_task_markers(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "grep"],
            returncode=0,
            stdout="docs/tasks/README.md:78:TODO marker\n",
            stderr="",
        )

        with patch("smartworkmate.proactive.subprocess.run", return_value=completed) as mock_run:
            findings = _git_code_findings(Path("."))

        self.assertEqual(findings, ["docs/tasks/README.md:78:TODO marker"])
        command = mock_run.call_args.args[0]
        self.assertEqual(command[:7], ["git", "grep", "-n", "-E", "TODO|FIXME|HACK", "--", "."])
        self.assertEqual(command[7:], list(FINDING_SCAN_EXCLUDES))

    def test_git_code_findings_deduplicates_and_trims_lines(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "grep"],
            returncode=0,
            stdout=(
                "docs/tasks/README.md:78:TODO marker\n"
                "\n"
                "  docs/tasks/README.md:78:TODO marker  \n"
                "smartworkmate/auto_runner.py:384:TODO marker\n"
            ),
            stderr="",
        )

        with patch("smartworkmate.proactive.subprocess.run", return_value=completed):
            findings = _git_code_findings(Path("."))

        self.assertEqual(
            findings,
            [
                "docs/tasks/README.md:78:TODO marker",
                "smartworkmate/auto_runner.py:384:TODO marker",
            ],
        )


class ProactiveBaseBranchTests(unittest.TestCase):
    def test_prefers_origin_head_branch(self) -> None:
        origin = subprocess.CompletedProcess(
            args=["git", "symbolic-ref"],
            returncode=0,
            stdout="origin/master\n",
            stderr="",
        )
        current = subprocess.CompletedProcess(
            args=["git", "branch"],
            returncode=0,
            stdout="feature/abc\n",
            stderr="",
        )

        with patch("smartworkmate.proactive.subprocess.run", side_effect=[origin, current]):
            branch = _detect_repo_base_branch(Path("."))

        self.assertEqual(branch, "master")

    def test_falls_back_to_current_branch_when_origin_head_missing(self) -> None:
        origin = subprocess.CompletedProcess(
            args=["git", "symbolic-ref"],
            returncode=1,
            stdout="",
            stderr="fatal",
        )
        current = subprocess.CompletedProcess(
            args=["git", "branch"],
            returncode=0,
            stdout="release/v1\n",
            stderr="",
        )

        with patch("smartworkmate.proactive.subprocess.run", side_effect=[origin, current]):
            branch = _detect_repo_base_branch(Path("."))

        self.assertEqual(branch, "release/v1")

    def test_returns_main_as_last_resort(self) -> None:
        origin = subprocess.CompletedProcess(
            args=["git", "symbolic-ref"],
            returncode=1,
            stdout="",
            stderr="fatal",
        )
        current = subprocess.CompletedProcess(
            args=["git", "branch"],
            returncode=0,
            stdout="\n",
            stderr="",
        )

        with patch("smartworkmate.proactive.subprocess.run", side_effect=[origin, current]):
            branch = _detect_repo_base_branch(Path("."))

        self.assertEqual(branch, "main")


if __name__ == "__main__":
    unittest.main()
