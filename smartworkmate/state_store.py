from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    status: str
    base_branch: str = "main"
    branch_name: str = ""
    worktree_name: str = ""
    last_run_id: str = ""
    session_id: str = ""
    thread_id: str = ""
    pr_url: str = ""
    approved_by: str = ""
    approved_at: str = ""
    notes: str = ""
    failure_type: str = ""
    failure_detail: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class State:
    tasks: dict[str, TaskRecord] = field(default_factory=dict)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> State:
        if not self.path.exists():
            return State()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        tasks = {
            key: TaskRecord(**value)
            for key, value in raw.get("tasks", {}).items()
        }
        return State(tasks=tasks)

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tasks": {
                key: asdict(record)
                for key, record in state.tasks.items()
            }
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert_task(
        self,
        state: State,
        *,
        task_id: str,
        status: str,
        base_branch: str,
        run_id: str,
        branch_name: str,
        worktree_name: str,
        session_id: str = "",
        thread_id: str = "",
        pr_url: str = "",
        approved_by: str = "",
        approved_at: str = "",
        notes: str = "",
        failure_type: str = "",
        failure_detail: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        previous = state.tasks.get(task_id)
        state.tasks[task_id] = TaskRecord(
            task_id=task_id,
            status=status,
            base_branch=base_branch or (previous.base_branch if previous else "main"),
            branch_name=branch_name,
            worktree_name=worktree_name,
            last_run_id=run_id,
            session_id=session_id or (previous.session_id if previous else ""),
            thread_id=thread_id or (previous.thread_id if previous else ""),
            pr_url=pr_url or (previous.pr_url if previous else ""),
            approved_by=approved_by or (previous.approved_by if previous else ""),
            approved_at=approved_at or (previous.approved_at if previous else ""),
            notes=notes or (previous.notes if previous else ""),
            failure_type=failure_type or (previous.failure_type if previous else ""),
            failure_detail=failure_detail or (previous.failure_detail if previous else ""),
            updated_at=now,
        )

    def update_task_status(
        self,
        state: State,
        *,
        task_id: str,
        status: str,
        pr_url: str = "",
        notes: str = "",
        failure_type: str = "",
        failure_detail: str = "",
    ) -> TaskRecord:
        previous = state.tasks.get(task_id)
        if previous is None:
            raise KeyError(f"Task {task_id} not found in state")
        now = datetime.now(timezone.utc).isoformat()
        updated = TaskRecord(
            task_id=previous.task_id,
            status=status,
            base_branch=previous.base_branch,
            branch_name=previous.branch_name,
            worktree_name=previous.worktree_name,
            last_run_id=previous.last_run_id,
            session_id=previous.session_id,
            thread_id=previous.thread_id,
            pr_url=pr_url or previous.pr_url,
            approved_by=previous.approved_by,
            approved_at=previous.approved_at,
            notes=notes or previous.notes,
            failure_type=failure_type,
            failure_detail=failure_detail,
            updated_at=now,
        )
        state.tasks[task_id] = updated
        return updated

    def set_task_approval(
        self,
        state: State,
        *,
        task_id: str,
        approved_by: str,
    ) -> TaskRecord:
        previous = state.tasks.get(task_id)
        if previous is None:
            raise KeyError(f"Task {task_id} not found in state")
        now = datetime.now(timezone.utc).isoformat()
        updated = TaskRecord(
            task_id=previous.task_id,
            status=previous.status,
            base_branch=previous.base_branch,
            branch_name=previous.branch_name,
            worktree_name=previous.worktree_name,
            last_run_id=previous.last_run_id,
            session_id=previous.session_id,
            thread_id=previous.thread_id,
            pr_url=previous.pr_url,
            approved_by=approved_by,
            approved_at=now,
            notes=previous.notes,
            failure_type=previous.failure_type,
            failure_detail=previous.failure_detail,
            updated_at=now,
        )
        state.tasks[task_id] = updated
        return updated
