from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import Task, TaskStatus


REQUIRED_SECTIONS = ("任务需求", "任务设计", "交付验收")
FIN_MARKER = "--FIN--"


class TaskFormatError(ValueError):
    pass


def load_tasks(tasks_dir: Path) -> list[Task]:
    if not tasks_dir.exists():
        return []

    tasks: list[Task] = []
    for path in sorted(tasks_dir.rglob("*.md")):
        name = path.name.lower()
        if name in {"readme.md", "template.md"}:
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
        labels=_as_str_list(meta.get("labels")),
        references=_as_str_list(meta.get("references")),
        path=path,
        requirements=sections["任务需求"].strip(),
        design=sections["任务设计"].strip(),
        acceptance_checks=acceptance_checks,
        finalized=_has_fin_marker(text),
    )


def _has_fin_marker(text: str) -> bool:
    return text.rstrip().endswith(FIN_MARKER)


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


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
    sections: dict[str, str] = {}
    for match in matches:
        raw_name = match.group(1).strip()
        normalized_name = _normalize_section_name(raw_name)
        sections[normalized_name] = match.group(2).strip()
    return sections


def _normalize_section_name(name: str) -> str:
    normalized = name.strip()
    normalized = re.sub(r"[\s\.:：。·、，]+$", "", normalized)
    return normalized


def _extract_checkbox_items(section_text: str) -> list[str]:
    lines = [line.strip() for line in section_text.splitlines()]
    out: list[str] = []
    for line in lines:
        if re.match(r"^-\s*\[(?: |x|X)\]", line):
            out.append(line[5:].strip())
    return out
