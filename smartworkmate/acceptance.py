from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TaskStatus
from .orchestrator import update_task_state
from .task_loader import load_tasks


BACKTICK_RE = re.compile(r"`([^`]+)`")


@dataclass(slots=True)
class CheckResult:
    check: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


def verify_task_acceptance(
    repo_root: Path,
    *,
    task_id: str,
    fail_on_manual_only: bool,
) -> dict[str, Any]:
    evaluation = evaluate_task_acceptance(
        repo_root,
        task_id=task_id,
        fail_on_manual_only=fail_on_manual_only,
    )

    state_result = update_task_state(
        repo_root,
        task_id=task_id,
        status=str(evaluation["status"]),
        pr_url="",
        notes=str(evaluation["notes"]),
    )

    return {
        "task_id": task_id,
        "status": evaluation["status"],
        "runnable_checks": evaluation["runnable_checks"],
        "manual_checks": evaluation["manual_checks"],
        "notes": evaluation["notes"],
        "state": state_result,
        "results": evaluation["results"],
    }


def evaluate_task_acceptance(
    repo_root: Path,
    *,
    task_id: str,
    fail_on_manual_only: bool,
) -> dict[str, Any]:
    tasks = load_tasks(repo_root / "docs" / "tasks")
    task = next((item for item in tasks if item.task_id == task_id), None)
    if task is None:
        raise KeyError(f"Task {task_id} not found in docs/tasks")

    runnable_commands: list[tuple[str, str]] = []
    manual_checks: list[str] = []
    for check in task.acceptance_checks:
        command = _extract_command(check)
        if command:
            if _is_recursive_verify_command(command, task.task_id):
                manual_checks.append(check + " (skipped recursive verify-task command)")
                continue
            runnable_commands.append((check, command))
        else:
            manual_checks.append(check)

    results: list[CheckResult] = []
    failed: list[CheckResult] = []

    for check, command in runnable_commands:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        result = CheckResult(
            check=check,
            command=command,
            exit_code=completed.returncode,
            stdout=_clip_output(completed.stdout),
            stderr=_clip_output(completed.stderr),
        )
        results.append(result)
        if result.exit_code != 0:
            failed.append(result)

    if failed:
        status = TaskStatus.REWORK.value
        notes = f"acceptance failed: {len(failed)}/{len(runnable_commands)} runnable checks"
    elif runnable_commands:
        status = TaskStatus.VERIFY.value
        notes = f"acceptance passed: {len(runnable_commands)} runnable checks"
    else:
        status = TaskStatus.BLOCKED.value if fail_on_manual_only else TaskStatus.VERIFY.value
        notes = "no runnable acceptance checks found"
    return {
        "task_id": task_id,
        "status": status,
        "runnable_checks": len(runnable_commands),
        "manual_checks": len(manual_checks),
        "notes": notes,
        "results": [
            {
                "check": item.check,
                "command": item.command,
                "exit_code": item.exit_code,
                "stdout": item.stdout,
                "stderr": item.stderr,
            }
            for item in results
        ],
    }


def _extract_command(check: str) -> str:
    match = BACKTICK_RE.search(check)
    if not match:
        return ""
    candidate = match.group(1).strip()
    if _looks_like_command(candidate):
        return candidate
    return ""


def _looks_like_command(value: str) -> bool:
    command_prefixes = (
        "uv ",
        "python ",
        "pytest ",
        "npm ",
        "pnpm ",
        "node ",
        "git ",
        "kimaki ",
        "opencode ",
        "bash ",
        "sh ",
        "pwsh ",
        "powershell ",
    )
    lowered = value.lower()
    if lowered.startswith(command_prefixes):
        return True
    if " " in value:
        return True
    if "&&" in value or "||" in value or "|" in value:
        return True
    return False


def _is_recursive_verify_command(command: str, task_id: str) -> bool:
    lowered = command.lower()
    if "verify-task" not in lowered:
        return False
    task_token = task_id.lower()
    return task_token in lowered


def _clip_output(text: str, max_chars: int = 1500) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"
