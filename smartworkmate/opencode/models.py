from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenCodeProject:
    id: str
    name: str
    worktree: str
    time_updated: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class OpenCodeSession:
    id: str
    directory: str
    title: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class OpenCodeTaskRecord:
    task_id: str
    path: str
    status: str
    mtime: float
