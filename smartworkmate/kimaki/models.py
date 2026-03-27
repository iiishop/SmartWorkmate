from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KimakiSendResult:
    thread_id: str | None
    session_id: str | None
    message_id: str | None
    stdout: str
    stderr: str
