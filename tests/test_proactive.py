from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from smartworkmate.proactive import FINDING_SCAN_EXCLUDES, _git_code_findings


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


if __name__ == "__main__":
    unittest.main()
