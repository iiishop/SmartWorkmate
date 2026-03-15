from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .state_store import StateStore, TaskRecord
from .task_loader import load_tasks


def sync_state_and_tasks(repo_root: Path) -> dict[str, Any]:
    return sync_state_and_tasks_with_options(repo_root)


def sync_state_and_tasks_with_options(
    repo_root: Path,
    *,
    force_state_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    tasks_dir = repo_root / "docs" / "tasks"
    tasks = load_tasks(tasks_dir)

    store = StateStore(repo_root / ".smartworkmate" / "state.json")
    state = store.load()

    updated_markdown: list[str] = []
    updated_state: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    forced = force_state_task_ids or set()

    for task in tasks:
        record = state.tasks.get(task.task_id)
        if record is None:
            state.tasks[task.task_id] = TaskRecord(
                task_id=task.task_id,
                status=task.status.value,
                base_branch=task.base_branch,
                updated_at=now,
            )
            updated_state.append(task.task_id)
            continue

        if _state_is_authoritative(record, task.task_id in forced):
            if task.status.value != record.status:
                _update_markdown_status(task.path, record.status)
                updated_markdown.append(task.task_id)
            continue

        if task.status.value != record.status:
            state.tasks[task.task_id] = TaskRecord(
                task_id=record.task_id,
                status=task.status.value,
                base_branch=task.base_branch or record.base_branch,
                branch_name=record.branch_name,
                worktree_name=record.worktree_name,
                last_run_id=record.last_run_id,
                session_id=record.session_id,
                thread_id=record.thread_id,
                pr_url=record.pr_url,
                approved_by=record.approved_by,
                approved_at=record.approved_at,
                notes=record.notes,
                updated_at=now,
            )
            updated_state.append(task.task_id)

    if updated_state:
        store.save(state)

    return {
        "updated_state": updated_state,
        "updated_markdown": updated_markdown,
        "tasks_seen": len(tasks),
    }


def _state_is_authoritative(record: TaskRecord, forced: bool) -> bool:
    if forced:
        return True
    return bool(
        record.last_run_id
        or record.branch_name
        or record.worktree_name
        or record.session_id
        or record.thread_id
        or record.pr_url
    )


def _update_markdown_status(path: Path, status: str) -> None:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    status_re = re.compile(r"^status\s*:\s*.*$", flags=re.MULTILINE)
    if status_re.search(frontmatter):
        new_frontmatter = status_re.sub(f"status: {status}", frontmatter)
    else:
        new_frontmatter = frontmatter.rstrip("\n") + f"\nstatus: {status}\n"
    new_frontmatter = new_frontmatter.rstrip("\n") + "\n"
    rebuilt = "---\n" + new_frontmatter + "---\n" + body
    path.write_text(rebuilt, encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("task file must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("task file frontmatter missing closing ---")
    frontmatter = text[4:end]
    body = text[end + 5 :]
    return frontmatter, body
