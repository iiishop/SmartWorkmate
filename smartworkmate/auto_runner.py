from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TaskStatus
from .orchestrator import (
    _detect_latest_kimaki_session,
    _resolve_kimaki_bin,
    build_run_context,
    select_next_task,
    write_run_context,
)
from .state_store import StateStore
from .task_loader import TaskFormatError, load_tasks


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(slots=True)
class ProjectTarget:
    directory: Path
    channel_id: str = ""
    channel_name: str = ""


def start_autonomous_runner(
    *,
    root: Path,
    execute: bool,
    once: bool,
    interval_seconds: int,
    user: str,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []

    while True:
        targets = discover_projects(root)
        cycle_result = _run_single_cycle(targets=targets, execute=execute, user=user)
        summaries.append(cycle_result)
        if once:
            break
        time.sleep(interval_seconds)

    return {
        "result": "completed_once" if once else "running",
        "execute": execute,
        "cycles": summaries,
    }


def discover_projects(root: Path) -> list[ProjectTarget]:
    targets_by_dir: dict[str, ProjectTarget] = {}

    kimaki_bin = _maybe_kimaki_bin()
    if kimaki_bin:
        kimaki_projects = _safe_json_command([kimaki_bin, "project", "list", "--json"], cwd=root)
        if isinstance(kimaki_projects, list):
            for item in kimaki_projects:
                if not isinstance(item, dict):
                    continue
                directory = Path(str(item.get("directory", ""))).resolve()
                key = str(directory)
                targets_by_dir[key] = ProjectTarget(
                    directory=directory,
                    channel_id=str(item.get("channel_id", "")),
                    channel_name=str(item.get("channel_name", "")),
                )

    opencode_bin = _maybe_opencode_bin()
    if opencode_bin:
        sessions = _safe_json_command(
            [opencode_bin, "session", "list", "--format", "json", "--max-count", "300"],
            cwd=root,
        )
        if isinstance(sessions, list):
            for item in sessions:
                if not isinstance(item, dict):
                    continue
                directory_raw = item.get("directory")
                if not isinstance(directory_raw, str) or not directory_raw:
                    continue
                directory = Path(directory_raw).resolve()
                key = str(directory)
                if key not in targets_by_dir:
                    targets_by_dir[key] = ProjectTarget(directory=directory)

    if not targets_by_dir and root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            tasks_dir = child / "docs" / "tasks"
            if tasks_dir.exists():
                targets_by_dir[str(child.resolve())] = ProjectTarget(directory=child.resolve())

    expanded: dict[str, ProjectTarget] = {}
    for target in targets_by_dir.values():
        _add_if_task_project(expanded, target)
        if (target.directory / "docs" / "tasks").exists():
            continue
        for tasks_dir in _find_tasks_dirs(target.directory, max_depth=4):
            project_dir = tasks_dir.parent.parent.resolve()
            inherited = ProjectTarget(
                directory=project_dir,
                channel_id=target.channel_id,
                channel_name=target.channel_name,
            )
            _add_if_task_project(expanded, inherited)

    if not expanded and root.exists():
        for tasks_dir in _find_tasks_dirs(root, max_depth=4):
            project_dir = tasks_dir.parent.parent.resolve()
            _add_if_task_project(expanded, ProjectTarget(directory=project_dir))

    return list(expanded.values())


def _add_if_task_project(targets: dict[str, ProjectTarget], target: ProjectTarget) -> None:
    if not (target.directory / "docs" / "tasks").exists():
        return
    key = str(target.directory)
    existing = targets.get(key)
    if existing is None:
        targets[key] = target
        return
    if not existing.channel_id and target.channel_id:
        targets[key] = target


def _find_tasks_dirs(root: Path, *, max_depth: int) -> list[Path]:
    skip_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".turbo",
    }
    root = root.resolve()
    root_parts = len(root.parts)
    found: list[Path] = []

    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.parts) - root_parts
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if depth > max_depth:
            dirs[:] = []
            continue
        if current_path.name == "tasks" and current_path.parent.name == "docs":
            found.append(current_path)
            dirs[:] = []

    return found


def _run_single_cycle(*, targets: list[ProjectTarget], execute: bool, user: str) -> dict[str, Any]:
    processed: list[dict[str, Any]] = []

    for target in targets:
        project_dir = target.directory
        tasks_dir = project_dir / "docs" / "tasks"
        if not tasks_dir.exists():
            continue

        try:
            tasks = load_tasks(tasks_dir)
        except TaskFormatError as error:
            processed.append(
                {
                    "project": str(project_dir),
                    "result": "task_format_error",
                    "error": str(error),
                }
            )
            continue

        task = select_next_task(tasks)
        if task is None:
            continue

        context = build_run_context(task, dry_run=not execute)
        context_path = write_run_context(project_dir, context)

        state_store = StateStore(project_dir / ".smartworkmate" / "state.json")
        state = state_store.load()
        state_store.upsert_task(
            state,
            task_id=task.task_id,
            status=TaskStatus.IN_PROGRESS.value if execute else TaskStatus.TODO.value,
            run_id=context.run_id,
            branch_name=context.branch_name,
            worktree_name=context.worktree_name,
        )
        state_store.save(state)

        if target.channel_id and _maybe_kimaki_bin():
            dispatch_output = _dispatch_via_kimaki(
                project_dir=project_dir,
                channel_id=target.channel_id,
                user=user,
                context=context,
                execute=execute,
            )
            session_id = ""
            thread_id = ""
            if execute:
                session_id, thread_id = _detect_latest_kimaki_session(project_dir, task.task_id)
                if session_id or thread_id:
                    state_store.upsert_task(
                        state,
                        task_id=task.task_id,
                        status=TaskStatus.IN_PROGRESS.value,
                        run_id=context.run_id,
                        branch_name=context.branch_name,
                        worktree_name=context.worktree_name,
                        session_id=session_id,
                        thread_id=thread_id,
                    )
                    state_store.save(state)

            processed.append(
                {
                    "project": str(project_dir),
                    "task_id": task.task_id,
                    "mode": "kimaki",
                    "run_id": context.run_id,
                    "context": str(context_path),
                    "thread_name": context.thread_name,
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "dispatch": dispatch_output,
                }
            )
            continue

        opencode_bin = _maybe_opencode_bin()
        if opencode_bin:
            dispatch_output = _dispatch_via_opencode(
                project_dir=project_dir,
                opencode_bin=opencode_bin,
                context=context,
                base_branch=task.base_branch,
                execute=execute,
            )
            processed.append(
                {
                    "project": str(project_dir),
                    "task_id": task.task_id,
                    "mode": "opencode",
                    "run_id": context.run_id,
                    "context": str(context_path),
                    "worktree": dispatch_output.get("worktree", ""),
                    "dispatch": dispatch_output.get("dispatch", ""),
                }
            )
            continue

        processed.append(
            {
                "project": str(project_dir),
                "task_id": task.task_id,
                "result": "no_dispatcher",
                "error": "Neither kimaki nor opencode is available",
            }
        )

    return {
        "targets": len(targets),
        "processed": processed,
    }


def _dispatch_via_kimaki(
    *,
    project_dir: Path,
    channel_id: str,
    user: str,
    context: Any,
    execute: bool,
) -> str:
    command = [
        _resolve_kimaki_bin(),
        "send",
        "--channel",
        channel_id,
        "--prompt",
        context.prompt,
        "--name",
        context.thread_name,
        "--worktree",
        context.worktree_name,
        "--user",
        user,
    ]
    if not execute:
        return "DRY-RUN: " + " ".join(command)

    completed = subprocess.run(
        command,
        cwd=project_dir,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return completed.stdout.strip()


def _dispatch_via_opencode(
    *,
    project_dir: Path,
    opencode_bin: str,
    context: Any,
    base_branch: str,
    execute: bool,
) -> dict[str, str]:
    worktree_root = project_dir.parent / f".{project_dir.name}-worktrees"
    worktree_dir = worktree_root / context.worktree_name

    git_command = [
        "git",
        "worktree",
        "add",
        "-B",
        context.branch_name,
        str(worktree_dir),
        base_branch,
    ]
    opencode_command = [opencode_bin, "run", "--agent", "build", "--prompt", context.prompt]

    if not execute:
        return {
            "worktree": str(worktree_dir),
            "dispatch": "DRY-RUN: " + " ".join(git_command) + " && " + " ".join(opencode_command),
        }

    worktree_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(git_command, cwd=project_dir, check=True, text=True, capture_output=True)
    completed = subprocess.run(
        opencode_command,
        cwd=worktree_dir,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    return {
        "worktree": str(worktree_dir),
        "dispatch": completed.stdout.strip(),
    }


def _safe_json_command(command: list[str], *, cwd: Path) -> Any:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except Exception:
        return []

    clean = ANSI_ESCAPE_RE.sub("", completed.stdout)
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("[") or stripped.startswith("{")):
            continue
        payload = "\n".join(lines[index:]).strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    return []


def _maybe_kimaki_bin() -> str:
    try:
        return _resolve_kimaki_bin()
    except Exception:
        return ""


def _maybe_opencode_bin() -> str:
    binary = shutil.which("opencode")
    if binary:
        return binary
    fallback = Path.home() / "AppData" / "Roaming" / "npm" / "opencode.CMD"
    if fallback.exists():
        return str(fallback)
    return ""
