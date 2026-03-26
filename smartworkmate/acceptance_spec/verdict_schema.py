from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


Status = Literal["pass", "fail", "error"]


@dataclass(slots=True)
class StatementResult:
    statement_index: int
    status: Status
    detail: str
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class SpecResult:
    spec_id: str
    status: Status
    statement_results: list[StatementResult]


@dataclass(slots=True)
class UnifiedVerdict:
    task_id: str
    run_id: str
    status: Status
    summary: str
    spec_results: list[SpecResult]
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
