from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smartworkmate.auto_runner import (
    _canonical_project_dir,
    _extract_thread_id_from_text,
    _extract_task_id_from_text,
    _maybe_gh_bin,
    _missing_pr_body_sections,
    _format_acceptance_notify,
    _should_use_kimaki_backend,
)
from smartworkmate.orchestrator import _detect_latest_kimaki_session
from smartworkmate.task_loader import load_task_file


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


class AcceptanceCheckboxParsingTests(unittest.TestCase):
    def test_accepts_checked_checkbox_items(self) -> None:
        content = """---
task_id: TSK-2026-123
title: checkbox parsing
---

## 任务需求

需求

## 任务设计

设计

## 交付验收

- [x] 第一条
- [ ] 第二条

--FIN--
"""
        with tempfile.TemporaryDirectory() as tmp:
            task_file = Path(tmp) / "TASK.md"
            task_file.write_text(content, encoding="utf-8")
            task = load_task_file(task_file)

        self.assertEqual(task.acceptance_checks, ["第一条", "第二条"])

    def test_accepts_section_heading_with_trailing_punctuation(self) -> None:
        content = """---
task_id: TSK-2026-124
title: section heading normalize
---

## 任务需求·

需求

## 任务设计：

设计

## 交付验收。

- [ ] 第一条

--FIN--
"""
        with tempfile.TemporaryDirectory() as tmp:
            task_file = Path(tmp) / "TASK.md"
            task_file.write_text(content, encoding="utf-8")
            task = load_task_file(task_file)

        self.assertEqual(task.requirements, "需求")
        self.assertEqual(task.design, "设计")
        self.assertEqual(task.acceptance_checks, ["第一条"])


class ProjectPathCanonicalizationTests(unittest.TestCase):
    def test_maps_new_worktree_path_back_to_repo_root_when_present(self) -> None:
        worktree = Path("D:/workspace/projects/SmartWorkmate/.smartworkmate/worktrees/auto-123")
        with patch("pathlib.Path.exists", return_value=True):
            canonical = _canonical_project_dir(worktree)

        self.assertEqual(canonical.name, "SmartWorkmate")
        self.assertEqual(canonical.parent.name, "projects")

    def test_maps_legacy_worktree_path_back_to_repo_root_when_present(self) -> None:
        worktree = Path("D:/workspace/projects/.SmartWorkmate-worktrees/auto-123")
        with patch("pathlib.Path.exists", return_value=True):
            canonical = _canonical_project_dir(worktree)

        self.assertEqual(canonical.name, "SmartWorkmate")
        self.assertEqual(canonical.parent.name, "projects")

    def test_keeps_original_legacy_path_when_repo_root_missing(self) -> None:
        worktree = Path("D:/workspace/projects/.SmartWorkmate-worktrees/auto-123")
        with patch("pathlib.Path.exists", return_value=False):
            canonical = _canonical_project_dir(worktree)

        self.assertIn(".SmartWorkmate-worktrees", str(canonical))


class BackendSelectionTests(unittest.TestCase):
    def test_auto_prefers_kimaki_when_available(self) -> None:
        self.assertTrue(
            _should_use_kimaki_backend(
                backend="auto",
                has_channel=True,
                kimaki_available=True,
                require_worktree_isolation=False,
            )
        )

    def test_auto_falls_back_when_channel_missing(self) -> None:
        self.assertFalse(
            _should_use_kimaki_backend(
                backend="auto",
                has_channel=False,
                kimaki_available=True,
                require_worktree_isolation=False,
            )
        )

    def test_explicit_opencode_disables_kimaki(self) -> None:
        self.assertFalse(
            _should_use_kimaki_backend(
                backend="opencode_local",
                has_channel=True,
                kimaki_available=True,
                require_worktree_isolation=False,
            )
        )

    def test_explicit_kimaki_requires_channel_and_binary(self) -> None:
        self.assertFalse(
            _should_use_kimaki_backend(
                backend="kimaki",
                has_channel=False,
                kimaki_available=True,
                require_worktree_isolation=False,
            )
        )
        self.assertFalse(
            _should_use_kimaki_backend(
                backend="kimaki",
                has_channel=True,
                kimaki_available=False,
                require_worktree_isolation=False,
            )
        )

    def test_worktree_isolation_forces_local_backend(self) -> None:
        self.assertFalse(
            _should_use_kimaki_backend(
                backend="kimaki",
                has_channel=True,
                kimaki_available=True,
                require_worktree_isolation=True,
            )
        )


class GhResolutionTests(unittest.TestCase):
    def test_prefers_path_lookup(self) -> None:
        with patch("smartworkmate.auto_runner.shutil.which", return_value="C:/bin/gh.exe"):
            resolved = _maybe_gh_bin()
        self.assertEqual(resolved, "C:/bin/gh.exe")

    def test_uses_standard_fallback_path(self) -> None:
        with patch("smartworkmate.auto_runner.shutil.which", return_value=""):
            with patch("pathlib.Path.exists", return_value=True):
                resolved = _maybe_gh_bin()
        self.assertIn("GitHub CLI", resolved)


class PrBodyQualityGateTests(unittest.TestCase):
    def test_reports_missing_sections(self) -> None:
        body = "## Summary\n- done\n"
        missing = _missing_pr_body_sections(body)
        self.assertIn("## Acceptance Mapping", missing)
        self.assertIn("## Concerns / Unfinished Items", missing)
        self.assertIn("## Reviewer Notes", missing)

    def test_passes_when_all_sections_present(self) -> None:
        body = (
            "## Summary\n- a\n\n"
            "## Acceptance Mapping\n- b\n\n"
            "## Concerns / Unfinished Items\n- c\n\n"
            "## Reviewer Notes\n- d\n"
        )
        missing = _missing_pr_body_sections(body)
        self.assertEqual(missing, [])


class LocalNotifyHelpersTests(unittest.TestCase):
    def test_extract_thread_id_from_discord_url(self) -> None:
        text = "URL: https://discord.com/channels/1/1482942268364165184"
        self.assertEqual(_extract_thread_id_from_text(text), "1482942268364165184")

    def test_format_acceptance_notify_includes_key_fields(self) -> None:
        message = _format_acceptance_notify(
            task_id="TSK-2026-001",
            status="verify",
            notes="all runnable checks passed",
            runnable=2,
            manual=1,
        )
        self.assertIn("TSK-2026-001", message)
        self.assertIn("verify", message)
        self.assertIn("runnable checks: 2", message)


if __name__ == "__main__":
    unittest.main()
