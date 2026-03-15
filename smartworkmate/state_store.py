from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    status: str
    branch_name: str = ""
    worktree_name: str = ""
    last_run_id: str = ""
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
        run_id: str,
        branch_name: str,
        worktree_name: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        state.tasks[task_id] = TaskRecord(
            task_id=task_id,
            status=status,
            branch_name=branch_name,
            worktree_name=worktree_name,
            last_run_id=run_id,
            updated_at=now,
        )
