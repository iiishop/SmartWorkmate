from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any

from .models import KimakiSendResult


_THREAD_ID_RE = re.compile(r"(?:thread(?:_id| id)?\s*[:=]\s*)([0-9]+)", re.IGNORECASE)
_SESSION_ID_RE = re.compile(
    r"(?:session(?:_id| id)?\s*[:=]\s*)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_MESSAGE_ID_RE = re.compile(r"(?:message(?:_id| id)?\s*[:=]\s*)([0-9]+)", re.IGNORECASE)


def build_send_optional_args(
    *,
    name: str | None = None,
    user: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    notify_only: bool = False,
    send_at: str | None = None,
    wait: bool = False,
) -> list[str]:
    args: list[str] = []
    if name:
        args.extend(["--name", name])
    if user:
        args.extend(["--user", user])
    if agent:
        args.extend(["--agent", agent])
    if model:
        args.extend(["--model", model])
    if notify_only:
        args.append("--notify-only")
    if send_at:
        args.extend(["--send-at", send_at])
    if wait:
        args.append("--wait")
    return args


def run_send(args: list[str]) -> KimakiSendResult:
    completed = run_kimaki(args)
    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "kimaki command failed"
        )
        raise RuntimeError(message)

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return KimakiSendResult(
        thread_id=_match(_THREAD_ID_RE, stdout),
        session_id=_match(_SESSION_ID_RE, stdout),
        message_id=_match(_MESSAGE_ID_RE, stdout),
        stdout=stdout,
        stderr=stderr,
    )


def run_json_command(args: list[str]) -> Any:
    completed = run_kimaki(args)
    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "kimaki command failed"
        )
        raise RuntimeError(message)
    payload = _extract_json_payload(completed.stdout or "")
    return json.loads(payload)


def resolve_project_directory(channel_id: str) -> str | None:
    rows = run_json_command(["project", "list", "--json"])
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("channel_id")) == channel_id:
            directory = row.get("directory")
            if isinstance(directory, str) and directory.strip():
                return directory
    return None


def list_project_sessions(project_directory: str) -> list[dict[str, Any]]:
    rows = run_json_command(
        ["session", "list", "--project", project_directory, "--json"]
    )
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def find_thread_id_in_sessions(
    sessions: list[dict[str, Any]],
    subthread_name: str,
) -> str | None:
    wanted = subthread_name.strip().casefold()
    for row in sessions:
        if str(row.get("source", "")).casefold() != "kimaki":
            continue
        title = str(row.get("title", "")).strip().casefold()
        thread_id = row.get("threadId")
        if title != wanted:
            continue
        if isinstance(thread_id, str) and thread_id.strip():
            return thread_id
    return None


def run_kimaki(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = _resolve_kimaki_executable()
    return subprocess.run(
        [*executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1)


def _resolve_kimaki_executable() -> list[str]:
    raw = os.environ.get("KIMAKI_EXECUTABLE", "kimaki")
    parts = shlex.split(raw)
    if not parts:
        raise ValueError("KIMAKI_EXECUTABLE must not be empty")
    return parts


def _extract_json_payload(stdout: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        return stdout[index : index + end]
    raise RuntimeError("kimaki output did not contain JSON payload")
