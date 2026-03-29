from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from smartworkmate.opencode import (
    read_task_acceptance,
    read_task_design,
    read_task_requirements,
)


def test_read_task_sections_returns_task_id_and_content(tmp_path: Path) -> None:
    task = _write_task(
        tmp_path,
        "TSK-2026-100-demo.md",
        task_id="TSK-2026-100",
        requirements="第一段需求\n\n第二段需求",
        design="第一段设计\n\n第二段设计",
        acceptance=_valid_asl(),
    )

    task_id_req, requirements = read_task_requirements(str(tmp_path), str(task))
    task_id_des, design = read_task_design(str(tmp_path), str(task))
    task_id_acc, acceptance = read_task_acceptance(str(tmp_path), str(task))

    assert task_id_req == "TSK-2026-100"
    assert task_id_des == "TSK-2026-100"
    assert task_id_acc == "TSK-2026-100"
    assert requirements == "第一段需求\n\n第二段需求"
    assert design == "第一段设计\n\n第二段设计"
    assert "using python;" in acceptance
    assert "expect {" in acceptance


def test_read_task_sections_ignore_heading_trailing_spaces(tmp_path: Path) -> None:
    task = tmp_path / "docs" / "tasks" / "TSK-2026-200.md"
    task.parent.mkdir(parents=True)
    task.write_text(
        "---\n"
        "task_id: TSK-2026-200\n"
        "title: Demo\n"
        "---\n\n"
        "## 任务需求   \n"
        "需求内容\n\n"
        "## 任务设计    \n"
        "设计内容\n\n"
        "## 交付验收   \n"
        "```asl\n"
        f"{_valid_asl()}\n"
        "```\n\n"
        "--FIN--",
        encoding="utf-8",
    )

    assert read_task_requirements(str(tmp_path), str(task))[1] == "需求内容"
    assert read_task_design(str(tmp_path), str(task))[1] == "设计内容"


def test_read_task_acceptance_raises_on_invalid_asl(tmp_path: Path) -> None:
    task = _write_task(
        tmp_path,
        "TSK-2026-300.md",
        task_id="TSK-2026-300",
        requirements="需求",
        design="设计",
        acceptance="using python;\nexpect {\n  broken == ;\n}",
    )

    with pytest.raises(ValueError, match="ASL"):
        read_task_acceptance(str(tmp_path), str(task))


def test_read_task_sections_raise_when_task_id_missing(tmp_path: Path) -> None:
    task = tmp_path / "docs" / "tasks" / "TSK-2026-400.md"
    task.parent.mkdir(parents=True)
    task.write_text(
        "---\n"
        "title: Demo\n"
        "---\n\n"
        "## 任务需求\nA\n\n"
        "## 任务设计\nB\n\n"
        "## 交付验收\n```asl\n"
        f"{_valid_asl()}\n"
        "```\n\n"
        "--FIN--",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="task_id"):
        read_task_requirements(str(tmp_path), str(task))


def test_duplicate_task_id_blocks_newer_task(tmp_path: Path) -> None:
    first = _write_task(
        tmp_path,
        "A.md",
        task_id="TSK-2026-500",
        requirements="A",
        design="A",
        acceptance=_valid_asl(),
    )
    second = _write_task(
        tmp_path,
        "B.md",
        task_id="TSK-2026-500",
        requirements="B",
        design="B",
        acceptance=_valid_asl(),
    )

    now = time.time()
    os.utime(first, (now - 60, now - 60))
    os.utime(second, (now, now))

    with pytest.raises(ValueError, match="blocked"):
        read_task_design(str(tmp_path), str(second))


def _write_task(
    root: Path,
    name: str,
    *,
    task_id: str,
    requirements: str,
    design: str,
    acceptance: str,
) -> Path:
    path = root / "docs" / "tasks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"task_id: {task_id}\n"
        "title: Demo\n"
        "---\n\n"
        "## 任务需求\n"
        f"{requirements}\n\n"
        "## 任务设计\n"
        f"{design}\n\n"
        "## 交付验收\n"
        "```asl\n"
        f"{acceptance}\n"
        "```\n\n"
        "--FIN--",
        encoding="utf-8",
    )
    return path


def _valid_asl() -> str:
    return (
        "using python;\n\n"
        "test algo.max_value as maxv;\n\n"
        "given {\n"
        "  A_small: int[] = [3, 1, 2];\n"
        "}\n\n"
        "expect {\n"
        "  maxv(A_small) == 3;\n"
        "}"
    )
