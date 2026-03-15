from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    VERIFY = "verify"
    PR_OPEN = "pr_open"
    DONE = "done"
    REWORK = "rework"
    BLOCKED = "blocked"


@dataclass(slots=True)
class Task:
    task_id: str
    title: str
    base_branch: str
    priority: str
    status: TaskStatus
    labels: list[str]
    references: list[str]
    path: Path
    requirements: str
    design: str
    acceptance_checks: list[str]
    finalized: bool


@dataclass(slots=True)
class RunContext:
    run_id: str
    task: Task
    worktree_name: str
    branch_name: str
    thread_name: str
    prompt: str
    dry_run: bool = True
    metadata: dict[str, str] = field(default_factory=dict)
