from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


NETWORK_FAILURE = "network_failure"
PERMISSION_FAILURE = "permission_failure"
TASK_FORMAT_FAILURE = "task_format_failure"
COMMAND_EXECUTION_FAILURE = "command_execution_failure"


@dataclass(slots=True)
class LockAcquireResult:
    acquired: bool
    status: str
    lock_path: Path
    owner_run_id: str = ""
    expires_at: str = ""


@dataclass(slots=True)
class RetryResult:
    success: bool
    attempts: int
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    failure_type: str = ""


class RuntimeCommandError(RuntimeError):
    def __init__(self, message: str, *, failure_type: str, attempts: int, command: list[str]) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.attempts = attempts
        self.command = command


def classify_failure(stderr: str, *, exit_code: int, stdout: str = "") -> str:
    haystack = f"{stderr}\n{stdout}".lower()
    network_keywords = (
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "temporary failure",
        "could not resolve host",
        "name or service not known",
        "network is unreachable",
        "tls handshake timeout",
        "eai_again",
        "enotfound",
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway timeout",
    )
    permission_keywords = (
        "permission denied",
        "access denied",
        "forbidden",
        "authentication failed",
        "unauthorized",
        "insufficient permission",
        "not permitted",
        "could not read from remote repository",
    )
    task_format_keywords = (
        "task file must start with yaml frontmatter",
        "task file frontmatter must end",
        "missing sections",
        "missing frontmatter fields",
        "taskformaterror",
        "section '交付验收' must contain checkbox items",
    )

    if any(keyword in haystack for keyword in network_keywords):
        return NETWORK_FAILURE
    if any(keyword in haystack for keyword in permission_keywords):
        return PERMISSION_FAILURE
    if any(keyword in haystack for keyword in task_format_keywords):
        return TASK_FORMAT_FAILURE
    if exit_code == 0:
        return ""
    return COMMAND_EXECUTION_FAILURE


def should_retry(failure_type: str) -> bool:
    return failure_type == NETWORK_FAILURE


def run_command_with_retry(
    command: list[str],
    *,
    cwd: Path,
    max_retries: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RetryResult:
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            completed = runner(
                command,
                cwd=cwd,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
        except OSError as error:
            failure_type = classify_failure(str(error), exit_code=1)
            if attempt < max_retries and should_retry(failure_type):
                delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
                time.sleep(delay)
                continue
            return RetryResult(
                success=False,
                attempts=attempts,
                command=command,
                returncode=1,
                stdout="",
                stderr=str(error),
                failure_type=failure_type,
            )

        failure_type = classify_failure(
            completed.stderr,
            exit_code=completed.returncode,
            stdout=completed.stdout,
        )
        if completed.returncode == 0:
            return RetryResult(
                success=True,
                attempts=attempts,
                command=command,
                returncode=0,
                stdout=completed.stdout,
                stderr=completed.stderr,
                failure_type="",
            )
        if attempt < max_retries and should_retry(failure_type):
            delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
            time.sleep(delay)
            continue
        return RetryResult(
            success=False,
            attempts=attempts,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            failure_type=failure_type,
        )

    return RetryResult(
        success=False,
        attempts=attempts,
        command=command,
        returncode=1,
        stdout="",
        stderr="retry exhausted",
        failure_type=COMMAND_EXECUTION_FAILURE,
    )


def run_or_raise(
    command: list[str],
    *,
    cwd: Path,
    max_retries: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
) -> RetryResult:
    result = run_command_with_retry(
        command,
        cwd=cwd,
        max_retries=max_retries,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )
    if result.success:
        return result
    message = (result.stderr or result.stdout or "command failed").strip()
    raise RuntimeCommandError(
        message,
        failure_type=result.failure_type or COMMAND_EXECUTION_FAILURE,
        attempts=result.attempts,
        command=command,
    )


def acquire_task_lock(
    repo_root: Path,
    *,
    task_id: str,
    run_id: str,
    ttl_seconds: int = 1800,
) -> LockAcquireResult:
    locks_dir = repo_root / ".smartworkmate" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{task_id}.lock"
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(60, ttl_seconds))
    payload = {
        "task_id": task_id,
        "run_id": run_id,
        "pid": os.getpid(),
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }

    for reclaim_attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2)
            return LockAcquireResult(
                acquired=True,
                status="acquired" if reclaim_attempt == 0 else "reclaimed",
                lock_path=lock_path,
                owner_run_id=run_id,
                expires_at=expires.isoformat(),
            )
        except FileExistsError:
            existing = _read_lock_payload(lock_path)
            should_reclaim = False
            if existing and _is_lock_expired(existing.get("expires_at", "")):
                should_reclaim = True
            if existing and not should_reclaim:
                pid_raw = existing.get("pid")
                try:
                    pid = int(pid_raw)
                except (TypeError, ValueError):
                    pid = 0
                if pid > 0 and not _is_process_alive(pid):
                    should_reclaim = True

            if should_reclaim:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            return LockAcquireResult(
                acquired=False,
                status="locked",
                lock_path=lock_path,
                owner_run_id=str(existing.get("run_id", "")) if existing else "",
                expires_at=str(existing.get("expires_at", "")) if existing else "",
            )

    return LockAcquireResult(
        acquired=False,
        status="locked",
        lock_path=lock_path,
    )


def release_task_lock(repo_root: Path, *, task_id: str, run_id: str) -> bool:
    lock_path = repo_root / ".smartworkmate" / "locks" / f"{task_id}.lock"
    if not lock_path.exists():
        return False
    existing = _read_lock_payload(lock_path)
    if existing and str(existing.get("run_id", "")) != run_id:
        return False
    try:
        lock_path.unlink()
        return True
    except OSError:
        return False


def _read_lock_payload(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _is_lock_expired(expires_at: str) -> bool:
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expiry


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
