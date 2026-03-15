from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from smartworkmate.runtime_guard import (
    COMMAND_EXECUTION_FAILURE,
    NETWORK_FAILURE,
    PERMISSION_FAILURE,
    TASK_FORMAT_FAILURE,
    acquire_task_lock,
    classify_failure,
    release_task_lock,
    run_command_with_retry,
)


class RuntimeGuardTests(unittest.TestCase):
    def test_classify_failure_types(self) -> None:
        self.assertEqual(
            classify_failure("connection timed out", exit_code=1),
            NETWORK_FAILURE,
        )
        self.assertEqual(
            classify_failure("permission denied", exit_code=1),
            PERMISSION_FAILURE,
        )
        self.assertEqual(
            classify_failure("missing frontmatter fields", exit_code=1),
            TASK_FORMAT_FAILURE,
        )
        self.assertEqual(
            classify_failure("unknown error", exit_code=1),
            COMMAND_EXECUTION_FAILURE,
        )

    def test_lock_is_idempotent_for_same_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            first = acquire_task_lock(repo, task_id="TSK-2026-002", run_id="run-a", ttl_seconds=300)
            self.assertTrue(first.acquired)

            second = acquire_task_lock(repo, task_id="TSK-2026-002", run_id="run-b", ttl_seconds=300)
            self.assertFalse(second.acquired)
            self.assertEqual(second.status, "locked")
            self.assertEqual(second.owner_run_id, "run-a")

            released = release_task_lock(repo, task_id="TSK-2026-002", run_id="run-a")
            self.assertTrue(released)

            third = acquire_task_lock(repo, task_id="TSK-2026-002", run_id="run-c", ttl_seconds=300)
            self.assertTrue(third.acquired)

    def test_retry_succeeds_after_network_failure(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=["cmd"],
                returncode=1,
                stdout="",
                stderr="connection timed out",
            ),
            subprocess.CompletedProcess(
                args=["cmd"],
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        ]

        def fake_runner(*_args, **_kwargs):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            result = run_command_with_retry(
                ["dummy", "command"],
                cwd=Path(tmp),
                max_retries=2,
                base_delay_seconds=0,
                runner=fake_runner,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)


if __name__ == "__main__":
    unittest.main()
