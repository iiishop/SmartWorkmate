from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smartworkmate.orchestrator import select_next_task
from smartworkmate.task_loader import load_task_file


TASK_TEMPLATE = """---
task_id: TSK-2026-999
title: Sample task
base_branch: main
priority: high
status: todo
labels: [test]
references:
  - ./README.md
---

## 任务需求

需要实现一个示例任务。

## 任务设计

按最小实现方式完成。

## 交付验收

- [ ] `python -m unittest -h`
"""


class FinMarkerTests(unittest.TestCase):
    def test_task_without_fin_marker_not_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.md"
            path.write_text(TASK_TEMPLATE, encoding="utf-8")
            task = load_task_file(path)
            self.assertFalse(task.finalized)

    def test_task_with_fin_marker_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.md"
            path.write_text(TASK_TEMPLATE + "\n--FIN--\n", encoding="utf-8")
            task = load_task_file(path)
            self.assertTrue(task.finalized)

    def test_select_next_task_only_returns_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "draft.md"
            final_path = Path(tmp) / "final.md"
            draft_path.write_text(TASK_TEMPLATE.replace("TSK-2026-999", "TSK-2026-998"), encoding="utf-8")
            final_path.write_text(TASK_TEMPLATE + "\n--FIN--\n", encoding="utf-8")
            draft_task = load_task_file(draft_path)
            final_task = load_task_file(final_path)
            selected = select_next_task([draft_task, final_task])
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected.task_id, "TSK-2026-999")


if __name__ == "__main__":
    unittest.main()
