from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import Task, TaskStatus


REQUIRED_SECTIONS = ("任务需求", "任务设计", "交付验收")


class TaskFormatError(ValueError):
    pass


def load_tasks(tasks_dir: Path) -> list[Task]:
    if not tasks_dir.exists():
        return []

    tasks: list[Task] = []
    for path in sorted(tasks_dir.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        task = load_task_file(path)
        tasks.append(task)
    return tasks


def load_task_file(path: Path) -> Task:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    meta = yaml.safe_load(frontmatter) or {}

    _validate_meta(path, meta)

    sections = _extract_sections(body)
    missing_sections = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing_sections:
        joined = ", ".join(missing_sections)
        raise TaskFormatError(f"{path}: missing sections: {joined}")

    acceptance_checks = _extract_checkbox_items(sections["交付验收"])
    if not acceptance_checks:
        raise TaskFormatError(f"{path}: section '交付验收' must contain checkbox items")

    return Task(
        task_id=str(meta["task_id"]),
        title=str(meta["title"]),
        base_branch=str(meta.get("base_branch", "main")),
        priority=str(meta.get("priority", "medium")),
        status=TaskStatus(str(meta.get("status", "todo"))),
        labels=[str(x) for x in meta.get("labels", [])],
        references=[str(x) for x in meta.get("references", [])],
        path=path,
        requirements=sections["任务需求"].strip(),
        design=sections["任务设计"].strip(),
        acceptance_checks=acceptance_checks,
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise TaskFormatError("Task file must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise TaskFormatError("Task file frontmatter must end with closing ---")
    frontmatter = text[4:end]
    body = text[end + 5 :]
    return frontmatter, body


def _validate_meta(path: Path, meta: dict[str, object]) -> None:
    required = ("task_id", "title")
    missing = [name for name in required if name not in meta]
    if missing:
        joined = ", ".join(missing)
        raise TaskFormatError(f"{path}: missing frontmatter fields: {joined}")


def _extract_sections(body: str) -> dict[str, str]:
    pattern = r"^##\s+(.+?)\n(.*?)(?=^##\s+|\Z)"
    matches = re.finditer(pattern, body, flags=re.MULTILINE | re.DOTALL)
    return {m.group(1).strip(): m.group(2).strip() for m in matches}


def _extract_checkbox_items(section_text: str) -> list[str]:
    lines = [line.strip() for line in section_text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line.startswith("- [ ]"):
            out.append(line[5:].strip())
    return out
