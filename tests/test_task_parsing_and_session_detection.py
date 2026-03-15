from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from smartworkmate.auto_runner import _extract_task_id_from_text
from smartworkmate.orchestrator import _detect_latest_kimaki_session


class TaskIdParsingTests(unittest.TestCase):
    def test_extracts_tsk_task_id(self) -> None:
        text = "broken task file: docs/tasks/TASK.md has id TSK-2026-001"
        self.assertEqual(_extract_task_id_from_text(text), "TSK-2026-001")

    def test_extracts_auto_task_id(self) -> None:
        text = "invalid frontmatter in AUTO-017579b8-maintenance.md"
        self.assertEqual(_extract_task_id_from_text(text), "AUTO-017579B8")


class SessionDetectionTests(unittest.TestCase):
    def test_ignore_unrelated_sessions_even_with_thread_id(self) -> None:
        payload = [
            {
                "id": "ses-unrelated",
                "threadId": "123",
                "source": "kimaki",
                "title": "General maintenance thread",
            },
            {
                "id": "ses-match",
                "threadId": "456",
                "source": "kimaki",
                "title": "AUTO-017579b8 | Auto maintenance review",
            },
        ]

        stdout = json.dumps(payload)

        with patch("smartworkmate.orchestrator._resolve_kimaki_bin", return_value="kimaki"):
            with patch("smartworkmate.orchestrator.subprocess.run") as mock_run:
                mock_run.return_value.stdout = stdout
                session_id, thread_id = _detect_latest_kimaki_session(Path("."), "AUTO-017579b8")

        self.assertEqual(session_id, "ses-match")
        self.assertEqual(thread_id, "456")


if __name__ == "__main__":
    unittest.main()
