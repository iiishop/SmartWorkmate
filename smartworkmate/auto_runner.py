from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import sys
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import evaluate_task_acceptance
from .models import TaskStatus
from .orchestrator import (
    _detect_latest_kimaki_session,
    _resolve_kimaki_bin,
    _send_non_interactive_followup,
    build_run_context,
    select_next_task,
    sync_task_from_kimaki,
    write_run_context,
)
from .state_store import StateStore, TaskRecord
from .status_sync import sync_state_and_tasks
from .task_loader import TaskFormatError, load_task_file, load_tasks
import yaml
from .proactive import create_idle_improvement_task, refresh_project_memory
from .runtime_guard import (
    COMMAND_EXECUTION_FAILURE,
    NETWORK_FAILURE,
    PERMISSION_FAILURE,
    TASK_FORMAT_FAILURE,
    RuntimeCommandError,
    acquire_task_lock,
    release_task_lock,
    run_or_raise,
)


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DISCORD_THREAD_URL_RE = re.compile(r"https://discord\.com/channels/\d+/(\d+)")
LIVE_HISTORY_LIMIT = 80
_LIVE_HISTORY: deque[str] = deque(maxlen=LIVE_HISTORY_LIMIT)
REQUIRED_PR_BODY_SECTIONS = (
    "## Summary",
    "## Acceptance Mapping",
    "## Concerns / Unfinished Items",
    "## Reviewer Notes",
)


class _Color:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"


@dataclass(slots=True)
class ProjectTarget:
    directory: Path
    channel_id: str = ""
    channel_name: str = ""


@dataclass(slots=True)
class ExecutionPolicy:
    backend: str
    require_worktree_isolation: bool
    auto_commit: bool


def start_autonomous_runner(
    *,
    root: Path,
    execute: bool,
    once: bool,
    interval_seconds: int,
    user: str,
    live_status: bool,
    opencode_global: bool,
) -> dict[str, Any]:
    _configure_console_output_utf8()
    summaries: list[dict[str, Any]] = []
    cycle_index = 0

    try:
        while True:
            cycle_index += 1
            targets = discover_projects(root, opencode_global=opencode_global)
            cycle_result = _run_single_cycle(targets=targets, execute=execute, user=user)
            summaries.append(cycle_result)
            if live_status:
                _render_live_status(
                    cycle_index=cycle_index,
                    cycle_result=cycle_result,
                    root=root,
                    execute=execute,
                    interval_seconds=interval_seconds,
                    seconds_to_next=0,
                )
            if once:
                break
            _sleep_with_heartbeat(
                interval_seconds,
                enabled=live_status,
                on_tick=(
                    lambda remaining: _render_live_status(
                        cycle_index=cycle_index,
                        cycle_result=cycle_result,
                        root=root,
                        execute=execute,
                        interval_seconds=interval_seconds,
                        seconds_to_next=remaining,
                    )
                ),
            )
    except KeyboardInterrupt:
        return {
            "result": "stopped",
            "execute": execute,
            "cycles": summaries,
        }

    return {
        "result": "completed_once" if once else "running",
        "execute": execute,
        "cycles": summaries,
    }


def discover_projects(root: Path, *, opencode_global: bool = False) -> list[ProjectTarget]:
    targets_by_dir: dict[str, ProjectTarget] = {}

    opencode_roots = _opencode_project_roots(root, scoped=not opencode_global)
    for directory in opencode_roots:
        canonical = _canonical_project_dir(directory)
        key = str(canonical)
        targets_by_dir[key] = ProjectTarget(directory=canonical)

    kimaki_bin = _maybe_kimaki_bin()
    if kimaki_bin:
        kimaki_projects = _safe_json_command([kimaki_bin, "project", "list", "--json"], cwd=root)
        if isinstance(kimaki_projects, list):
            for item in kimaki_projects:
                if not isinstance(item, dict):
                    continue
                directory_raw = item.get("directory")
                if not isinstance(directory_raw, str) or not directory_raw.strip():
                    continue
                directory = _canonical_project_dir(Path(directory_raw.strip()))
                key = str(directory)
                if opencode_global and key not in targets_by_dir:
                    continue
                existing = targets_by_dir.get(key)
                if existing is None:
                    targets_by_dir[key] = ProjectTarget(
                        directory=directory,
                        channel_id=str(item.get("channel_id", "")),
                        channel_name=str(item.get("channel_name", "")),
                    )
                else:
                    existing.channel_id = str(item.get("channel_id", ""))
                    existing.channel_name = str(item.get("channel_name", ""))

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
                directory = _canonical_project_dir(Path(directory_raw))
                key = str(directory)
                if key not in targets_by_dir:
                    targets_by_dir[key] = ProjectTarget(directory=directory)

    allow_root_fallback = not opencode_global or not opencode_roots
    if allow_root_fallback and not targets_by_dir and root.exists():
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
            project_dir = _canonical_project_dir(tasks_dir.parent.parent)
            inherited = ProjectTarget(
                directory=project_dir,
                channel_id=target.channel_id,
                channel_name=target.channel_name,
            )
            _add_if_task_project(expanded, inherited)

    if allow_root_fallback and not expanded and root.exists():
        for tasks_dir in _find_tasks_dirs(root, max_depth=4):
            project_dir = _canonical_project_dir(tasks_dir.parent.parent)
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


def _opencode_project_roots(scope_root: Path, *, scoped: bool = True) -> list[Path]:
    opencode_bin = _maybe_opencode_bin()
    if not opencode_bin:
        return []

    projects = _safe_json_command(
        [
            opencode_bin,
            "db",
            "SELECT worktree FROM project WHERE worktree IS NOT NULL AND worktree != '/' ORDER BY time_updated DESC;",
            "--format",
            "json",
        ],
        cwd=scope_root,
    )
    if not isinstance(projects, list):
        return []

    roots: list[Path] = []
    seen: set[str] = set()
    scope = scope_root.resolve()
    for item in projects:
        if not isinstance(item, dict):
            continue
        raw = item.get("worktree")
        if not isinstance(raw, str) or not raw.strip():
            continue
        directory = _canonical_project_dir(Path(raw))
        directory_key = str(directory)
        if directory_key in seen:
            continue
        if scoped and not _is_same_or_child(scope, directory):
            continue
        seen.add(directory_key)
        roots.append(directory)

    return roots


def _canonical_project_dir(directory: Path) -> Path:
    resolved = directory.resolve()
    parts = list(resolved.parts)

    # New preferred layout: <repo>/.smartworkmate/worktrees/<task-worktree>
    for index in range(len(parts) - 2):
        if parts[index].lower() != ".smartworkmate":
            continue
        if parts[index + 1].lower() != "worktrees":
            continue
        base = _path_from_parts(parts[:index])
        if base is not None and base.exists():
            return base.resolve()
        break

    # Backward compatibility: <parent>/.<repo>-worktrees/<task-worktree>
    for index, part in enumerate(parts):
        lowered = part.lower()
        if not (lowered.startswith(".") and lowered.endswith("-worktrees")):
            continue
        project_name = part[1 : len(part) - len("-worktrees")]
        base = _path_from_parts(parts[:index])
        if base is None:
            break
        candidate = (base / project_name).resolve()
        if candidate.exists():
            return candidate
        break
    return resolved


def _path_from_parts(parts: list[str]) -> Path | None:
    if not parts:
        return None
    root = Path(parts[0])
    for part in parts[1:]:
        root = root / part
    return root


def _is_same_or_child(parent: Path, child: Path) -> bool:
    parent_str = os.path.normcase(str(parent.resolve()))
    child_str = os.path.normcase(str(child.resolve()))
    if parent_str == child_str:
        return True
    return child_str.startswith(parent_str.rstrip("\\/") + os.sep)


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
        policy = _load_execution_policy(project_dir)

        sync_result = sync_state_and_tasks(project_dir)
        processed.append(
                {
                    "project": str(project_dir),
                    "mode": "state_markdown_sync",
                    "result": sync_result,
                    "policy": {
                        "backend": policy.backend,
                        "require_worktree_isolation": policy.require_worktree_isolation,
                        "auto_commit": policy.auto_commit,
                    },
                }
            )

        memory_result = refresh_project_memory(project_dir)
        processed.append(
            {
                "project": str(project_dir),
                "mode": "memory_refresh",
                "result": memory_result,
            }
        )

        reconciliation = _reconcile_project_tasks(project_dir, execute=execute)
        if reconciliation["events"]:
            processed.append(
                {
                    "project": str(project_dir),
                    "mode": "reconcile",
                    "events": reconciliation["events"],
                    "reliability": {
                        "lock": "enabled",
                        "retry": "enabled",
                        "reconcile": "active",
                    },
                }
            )
        if reconciliation["active_task_ids"]:
            state_store = StateStore(project_dir / ".smartworkmate" / "state.json")
            state = state_store.load()
            for active_task_id in reconciliation["active_task_ids"]:
                record = state.tasks.get(active_task_id)
                if record is None:
                    continue
                state_store.update_task_status(
                    state,
                    task_id=active_task_id,
                    status=record.status,
                    pr_url=record.pr_url,
                    notes="locked: active task already in progress/reconcile",
                    failure_type="",
                    failure_detail="",
                )
            state_store.save(state)
            processed.append(
                {
                    "project": str(project_dir),
                    "result": "waiting_active_tasks",
                    "active_task_ids": reconciliation["active_task_ids"],
                    "lock_state": "active_guard_locked",
                    "reliability": {
                        "lock": "enabled",
                        "retry": "enabled",
                        "reconcile": "active",
                    },
                }
            )
            continue

        try:
            tasks = load_tasks(tasks_dir)
        except TaskFormatError as error:
            format_task_id = _extract_task_id_from_text(str(error))
            if format_task_id:
                state_store = StateStore(project_dir / ".smartworkmate" / "state.json")
                state = state_store.load()
                state_store.upsert_task(
                    state,
                    task_id=format_task_id,
                    status=TaskStatus.BLOCKED.value,
                    base_branch="main",
                    run_id=f"format-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
                    branch_name="",
                    worktree_name="",
                    notes=str(error),
                    failure_type=TASK_FORMAT_FAILURE,
                    failure_detail=str(error),
                )
                state_store.save(state)
            processed.append(
                {
                    "project": str(project_dir),
                    "result": "task_format_error",
                    "error": str(error),
                    "failure_type": TASK_FORMAT_FAILURE,
                }
            )
            continue

        task = select_next_task(tasks)
        if task is None:
            if execute:
                idle_result = create_idle_improvement_task(project_dir, max_commits=20)
                processed.append(
                    {
                        "project": str(project_dir),
                        "mode": "idle_task",
                        "result": idle_result,
                    }
                )
            continue

        state_store = StateStore(project_dir / ".smartworkmate" / "state.json")
        state = state_store.load()

        context = build_run_context(task, dry_run=not execute, repo_root=project_dir)
        lock = acquire_task_lock(
            project_dir,
            task_id=task.task_id,
            run_id=context.run_id,
            ttl_seconds=1800,
        )
        if not lock.acquired:
            processed.append(
                {
                    "project": str(project_dir),
                    "task_id": task.task_id,
                    "result": "skipped_locked",
                    "reliability": {
                        "lock": lock.status,
                        "owner_run_id": lock.owner_run_id,
                        "expires_at": lock.expires_at,
                    },
                }
            )
            continue

        context_path = write_run_context(project_dir, context)
        state_store.upsert_task(
            state,
            task_id=task.task_id,
            status=TaskStatus.IN_PROGRESS.value if execute else TaskStatus.TODO.value,
            base_branch=task.base_branch,
            run_id=context.run_id,
            branch_name=context.branch_name,
            worktree_name=context.worktree_name,
            failure_type="",
            failure_detail="",
        )
        state_store.save(state)

        try:
            use_kimaki_backend = _should_use_kimaki_backend(
                backend=policy.backend,
                has_channel=bool(target.channel_id),
                kimaki_available=bool(_maybe_kimaki_bin()),
                require_worktree_isolation=bool(policy.require_worktree_isolation),
            )

            if use_kimaki_backend and target.channel_id and _maybe_kimaki_bin():
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
                            base_branch=task.base_branch,
                            run_id=context.run_id,
                            branch_name=context.branch_name,
                            worktree_name=context.worktree_name,
                            session_id=session_id,
                            thread_id=thread_id,
                            failure_type="",
                            failure_detail="",
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
                        "acceptance": "pending_async_session_completion",
                        "reliability": {
                            "lock": lock.status,
                            "retry": "enabled",
                            "reconcile": "enabled",
                        },
                    }
                )
                continue

            opencode_bin = _maybe_opencode_bin()
            if opencode_bin:
                notify_thread_id = ""
                if execute and target.channel_id and _maybe_kimaki_bin():
                    notify_thread_id = _open_notify_thread_for_local_execution(
                        project_dir=project_dir,
                        channel_id=target.channel_id,
                        user=user,
                        task_id=task.task_id,
                        thread_name=context.thread_name,
                    )
                    if notify_thread_id:
                        state_store.upsert_task(
                            state,
                            task_id=task.task_id,
                            status=TaskStatus.IN_PROGRESS.value,
                            base_branch=task.base_branch,
                            run_id=context.run_id,
                            branch_name=context.branch_name,
                            worktree_name=context.worktree_name,
                            thread_id=notify_thread_id,
                            failure_type="",
                            failure_detail="",
                        )
                        state_store.save(state)

                dispatch_output = _dispatch_via_opencode(
                    project_dir=project_dir,
                    opencode_bin=opencode_bin,
                    context=context,
                    base_branch=task.base_branch,
                    execute=execute,
                    auto_commit=policy.auto_commit,
                )

                acceptance_summary: dict[str, Any] | None = None
                if execute:
                    worktree_path = Path(dispatch_output.get("worktree", ""))
                    if worktree_path.exists():
                        acceptance_summary = evaluate_task_acceptance(
                            worktree_path,
                            task_id=task.task_id,
                            fail_on_manual_only=False,
                        )
                        state_store.update_task_status(
                            state,
                            task_id=task.task_id,
                            status=str(acceptance_summary["status"]),
                            notes=f"auto acceptance on worktree: {acceptance_summary['notes']}",
                            failure_type="",
                            failure_detail="",
                        )
                        state_store.save(state)
                        if notify_thread_id:
                            _post_notify_thread_update(
                                project_dir=project_dir,
                                thread_id=notify_thread_id,
                                message=_format_acceptance_notify(
                                    task_id=task.task_id,
                                    status=str(acceptance_summary["status"]),
                                    notes=str(acceptance_summary["notes"]),
                                    runnable=int(acceptance_summary["runnable_checks"]),
                                    manual=int(acceptance_summary["manual_checks"]),
                                ),
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
                        "thread_id": notify_thread_id,
                        "acceptance": acceptance_summary,
                        "reliability": {
                            "lock": lock.status,
                            "retry": "enabled",
                            "reconcile": "enabled",
                        },
                    }
                )
                continue
        except RuntimeCommandError as error:
            failure_status = _status_for_failure_type(error.failure_type, execute=execute)
            state_store.update_task_status(
                state,
                task_id=task.task_id,
                status=failure_status,
                notes=f"dispatch failed after {error.attempts} attempts: {str(error)}",
                failure_type=error.failure_type,
                failure_detail=str(error),
            )
            state_store.save(state)
            processed.append(
                {
                    "project": str(project_dir),
                    "task_id": task.task_id,
                    "result": "dispatch_failed",
                    "error": str(error),
                    "failure_type": error.failure_type,
                    "attempts": error.attempts,
                    "reliability": {
                        "lock": lock.status,
                        "retry": "enabled",
                        "reconcile": "enabled",
                    },
                }
            )
            continue
        finally:
            release_task_lock(project_dir, task_id=task.task_id, run_id=context.run_id)

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


def _reconcile_project_tasks(project_dir: Path, *, execute: bool) -> dict[str, Any]:
    store = StateStore(project_dir / ".smartworkmate" / "state.json")
    state = store.load()

    active_statuses = {
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.PR_OPEN.value,
        TaskStatus.VERIFY.value,
    }
    active_records = [record for record in state.tasks.values() if record.status in active_statuses]
    events: list[dict[str, Any]] = []

    for record in active_records:
        event: dict[str, Any] = {
            "task_id": record.task_id,
            "status": record.status,
        }

        if _maybe_kimaki_bin():
            sync_result = sync_task_from_kimaki(project_dir, task_id=record.task_id)
            event["sync"] = sync_result

        refreshed_state = store.load()
        refreshed = refreshed_state.tasks.get(record.task_id)
        if execute and refreshed and not refreshed.pr_url:
            pr_attempt = _ensure_pull_request(project_dir, refreshed)
            event["auto_pr"] = pr_attempt
            if pr_attempt.get("url"):
                store.update_task_status(
                    refreshed_state,
                    task_id=record.task_id,
                    status=TaskStatus.PR_OPEN.value,
                    pr_url=str(pr_attempt["url"]),
                    notes="auto PR created during reconcile",
                )
                store.save(refreshed_state)
                refreshed_state = store.load()
                refreshed = refreshed_state.tasks.get(record.task_id)
            elif pr_attempt.get("result") in {"push_failed", "create_failed"}:
                failure_type = str(pr_attempt.get("failure_type", COMMAND_EXECUTION_FAILURE))
                store.update_task_status(
                    refreshed_state,
                    task_id=record.task_id,
                    status=_status_for_failure_type(failure_type, execute=execute),
                    pr_url=refreshed.pr_url if refreshed else "",
                    notes=f"auto PR failed: {pr_attempt.get('reason', 'unknown')}",
                    failure_type=failure_type,
                    failure_detail=str(pr_attempt.get("reason", "unknown")),
                )
                store.save(refreshed_state)
                refreshed_state = store.load()
                refreshed = refreshed_state.tasks.get(record.task_id)

        if refreshed and refreshed.pr_url:
            verify_root = _resolve_verification_root(project_dir, refreshed)
            _sync_task_file_for_acceptance(
                project_dir=project_dir,
                verify_root=verify_root,
                task_id=record.task_id,
            )
            acceptance = evaluate_task_acceptance(
                verify_root,
                task_id=record.task_id,
                fail_on_manual_only=False,
            )
            store.update_task_status(
                refreshed_state,
                task_id=record.task_id,
                status=str(acceptance["status"]),
                pr_url=refreshed.pr_url,
                notes=f"auto reconcile: {acceptance['notes']}",
            )
            store.save(refreshed_state)

            event["acceptance"] = {
                "status": acceptance["status"],
                "runnable_checks": acceptance["runnable_checks"],
                "manual_checks": acceptance["manual_checks"],
                "notes": acceptance["notes"],
                "verify_root": str(verify_root),
            }

        refreshed_state = store.load()
        refreshed = refreshed_state.tasks.get(record.task_id)
        if refreshed and refreshed.status == TaskStatus.VERIFY.value and refreshed.pr_url:
            if _manual_approval_required(project_dir) and not refreshed.approved_at:
                event["done_gate"] = {
                    "result": "awaiting_manual_approval",
                    "required": True,
                }
            else:
                done_notes = "done after verify and PR"
                if refreshed.approved_by:
                    done_notes += f" (approved by {refreshed.approved_by})"
                store.update_task_status(
                    refreshed_state,
                    task_id=record.task_id,
                    status=TaskStatus.DONE.value,
                    pr_url=refreshed.pr_url,
                    notes=done_notes,
                )
                store.save(refreshed_state)
                event["done_gate"] = {
                    "result": "done",
                    "required": _manual_approval_required(project_dir),
                }

        events.append(event)

    latest_state = store.load()
    remaining_active = [
        item.task_id for item in latest_state.tasks.values() if item.status in active_statuses
    ]
    return {
        "events": events,
        "active_task_ids": remaining_active,
    }


def _manual_approval_required(project_dir: Path) -> bool:
    config_path = project_dir / ".smartworkmate" / "config.yaml"
    if not config_path.exists():
        return True
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return True
    value = data.get("manual_approval_required", True)
    return bool(value)


def _resolve_verification_root(project_dir: Path, record: TaskRecord) -> Path:
    worktree_paths = _git_worktree_paths(project_dir)
    branch = record.branch_name.strip()

    if branch:
        for item in worktree_paths:
            item_branch = item.get("branch", "")
            if item_branch.endswith(branch):
                path = Path(str(item.get("path", "")))
                if path.exists():
                    return path

    conventional = _worktree_root(project_dir) / record.worktree_name
    if record.worktree_name and conventional.exists():
        return conventional

    return project_dir


def _git_worktree_paths(project_dir: Path) -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=project_dir,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except Exception:
        return []

    blocks = completed.stdout.strip().split("\n\n")
    entries: list[dict[str, str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        item: dict[str, str] = {}
        for line in lines:
            if line.startswith("worktree "):
                item["path"] = line.replace("worktree ", "", 1)
            elif line.startswith("branch "):
                item["branch"] = line.replace("branch ", "", 1)
        if item:
            entries.append(item)
    return entries


def _ensure_pull_request(project_dir: Path, record: TaskRecord) -> dict[str, str]:
    branch = record.branch_name.strip()
    base = record.base_branch.strip() or "main"
    if not branch:
        return {"result": "skipped", "reason": "missing branch name"}

    branch_guard = _validate_branch_ready_for_pr(project_dir, base=base, branch=branch)
    if branch_guard.get("ok") != "true":
        return {
            "result": "not_ready",
            "failure_type": branch_guard.get("failure_type", ""),
            "reason": branch_guard.get("reason", "branch not ready for PR"),
        }

    existing = _gh_pr_view(project_dir, branch)
    if existing:
        return {"result": "exists", "url": existing}

    push_result = _push_branch(project_dir, branch)
    if push_result.get("result") != "ok":
        return {
            "result": "push_failed",
            "failure_type": push_result.get("failure_type", COMMAND_EXECUTION_FAILURE),
            "reason": push_result.get("reason", "unknown"),
        }

    task = _find_task_by_id(project_dir, record.task_id)
    title = (
        f"[{record.task_id}] {task.title}"[:120]
        if task is not None
        else f"[{record.task_id}] Automated task implementation"
    )
    body = _build_auto_pr_body(record=record, task=task)
    create_result = _gh_pr_create(project_dir, base=base, head=branch, title=title, body=body)
    if create_result.get("url"):
        return {"result": "created", "url": str(create_result["url"])}
    return {
        "result": "create_failed",
        "failure_type": create_result.get("failure_type", COMMAND_EXECUTION_FAILURE),
        "reason": create_result.get("reason", "unknown"),
    }


def _push_branch(project_dir: Path, branch: str) -> dict[str, str]:
    try:
        run_or_raise(
            ["git", "push", "-u", "origin", branch],
            cwd=project_dir,
            max_retries=3,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
        )
        return {"result": "ok"}
    except RuntimeCommandError as error:
        return {
            "result": "error",
            "failure_type": error.failure_type,
            "reason": str(error),
        }


def _find_task_by_id(project_dir: Path, task_id: str) -> Any | None:
    tasks_dir = project_dir / "docs" / "tasks"
    if not tasks_dir.exists():
        return None
    for path in sorted(tasks_dir.rglob("*.md")):
        name = path.name.lower()
        if name in {"readme.md", "template.md"}:
            continue
        try:
            task = load_task_file(path)
        except TaskFormatError:
            continue
        if task.task_id == task_id:
            return task
    return None


def _build_auto_pr_body(*, record: TaskRecord, task: Any | None) -> str:
    title = task.title if task is not None else "(unknown title)"
    acceptance = task.acceptance_checks if task is not None else []
    acceptance_lines = (
        "\n".join(f"- [ ] {item}" for item in acceptance)
        if acceptance
        else "- [ ] 未读取到任务验收项，请人工补充"
    )
    unresolved: list[str] = []
    if record.status in {TaskStatus.BLOCKED.value, TaskStatus.REWORK.value}:
        unresolved.append(record.notes.strip() or "任务当前状态不是可直接完成，需人工复核")
    if not unresolved:
        unresolved.append("暂无显式阻塞；请结合 diff 和测试记录做最终人工确认")

    unresolved_lines = "\n".join(f"- {item}" for item in unresolved)
    return (
        "## Summary\n"
        f"- Task: {record.task_id} - {title}\n"
        "- This PR is auto-created by SmartWorkmate reconcile loop\n"
        "- Changes are prepared for reviewer validation before merge\n\n"
        "## Acceptance Mapping\n"
        f"{acceptance_lines}\n\n"
        "## Concerns / Unfinished Items\n"
        f"{unresolved_lines}\n\n"
        "## Reviewer Notes\n"
        "- Review implementation completeness against docs/tasks requirements\n"
        "- Confirm tests and runtime checks pass in your environment\n"
        "- If scope cannot be fully completed, keep unresolved items in this PR description\n"
    )


def _gh_pr_view(project_dir: Path, branch: str) -> str:
    gh_bin = _maybe_gh_bin()
    if not gh_bin:
        return ""
    try:
        completed = subprocess.run(
            [gh_bin, "pr", "view", branch, "--json", "url"],
            cwd=project_dir,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except Exception:
        return ""

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""
    url = payload.get("url") if isinstance(payload, dict) else ""
    return str(url or "")


def _gh_pr_create(project_dir: Path, *, base: str, head: str, title: str, body: str) -> dict[str, str]:
    missing_sections = _missing_pr_body_sections(body)
    if missing_sections:
        return {
            "failure_type": COMMAND_EXECUTION_FAILURE,
            "reason": "PR body missing required sections: " + ", ".join(missing_sections),
        }

    gh_bin = _maybe_gh_bin()
    if not gh_bin:
        return {
            "failure_type": COMMAND_EXECUTION_FAILURE,
            "reason": "gh CLI not found in PATH or standard install paths",
        }
    try:
        completed = run_or_raise(
            [gh_bin, "pr", "create", "--base", base, "--head", head, "--title", title, "--body", body],
            cwd=project_dir,
            max_retries=3,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
        )
    except RuntimeCommandError as error:
        return {
            "failure_type": error.failure_type,
            "reason": str(error),
        }
    output = completed.stdout.strip()
    for line in output.splitlines():
        if line.startswith("https://"):
            return {"url": line.strip()}
    return {"reason": output or "missing PR URL in gh output"}


def _missing_pr_body_sections(body: str) -> list[str]:
    text = body or ""
    return [section for section in REQUIRED_PR_BODY_SECTIONS if section not in text]


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
        "--agent",
        "build",
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

    completed = _run_or_raise_with_progress(
        command,
        cwd=project_dir,
        max_retries=3,
        base_delay_seconds=1.0,
        max_delay_seconds=8.0,
        label=f"kimaki:{context.task.task_id}",
    )

    _send_non_interactive_followup(
        repo_root=project_dir,
        task_id=context.task.task_id,
        full_prompt=context.prompt,
    )

    return completed.stdout.strip()


def _open_notify_thread_for_local_execution(
    *,
    project_dir: Path,
    channel_id: str,
    user: str,
    task_id: str,
    thread_name: str,
) -> str:
    command = [
        _resolve_kimaki_bin(),
        "send",
        "--channel",
        channel_id,
        "--prompt",
        f"[local-exec] {task_id} 已启动本地 worktree 执行。后续进度将持续更新。",
        "--name",
        thread_name,
        "--notify-only",
        "--user",
        user,
    ]
    try:
        completed = run_or_raise(
            command,
            cwd=project_dir,
            max_retries=2,
            base_delay_seconds=1.0,
            max_delay_seconds=4.0,
        )
    except RuntimeCommandError:
        return ""
    return _extract_thread_id_from_text(completed.stdout)


def _post_notify_thread_update(*, project_dir: Path, thread_id: str, message: str) -> None:
    if not thread_id:
        return
    command = [
        _resolve_kimaki_bin(),
        "send",
        "--thread",
        thread_id,
        "--prompt",
        message,
        "--notify-only",
    ]
    subprocess.run(
        command,
        cwd=project_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _format_acceptance_notify(*, task_id: str, status: str, notes: str, runnable: int, manual: int) -> str:
    return (
        f"[local-exec] {task_id} 进度更新\n"
        f"- acceptance status: {status}\n"
        f"- runnable checks: {runnable}\n"
        f"- manual checks: {manual}\n"
        f"- notes: {notes}"
    )


def _extract_thread_id_from_text(text: str) -> str:
    match = DISCORD_THREAD_URL_RE.search(text or "")
    if not match:
        return ""
    return match.group(1)


def _dispatch_via_opencode(
    *,
    project_dir: Path,
    opencode_bin: str,
    context: Any,
    base_branch: str,
    execute: bool,
    auto_commit: bool,
) -> dict[str, str]:
    worktree_root = _worktree_root(project_dir)
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
    opencode_command = [opencode_bin, "run", "--agent", "build", context.prompt]

    if not execute:
        return {
            "worktree": str(worktree_dir),
            "dispatch": "DRY-RUN: " + " ".join(git_command) + " && " + " ".join(opencode_command),
        }

    worktree_root.mkdir(parents=True, exist_ok=True)
    _ensure_worktree_available(project_dir=project_dir, worktree_dir=worktree_dir, branch_name=context.branch_name)
    try:
        run_or_raise(
            git_command,
            cwd=project_dir,
            max_retries=1,
            base_delay_seconds=0.5,
            max_delay_seconds=1.0,
        )
    except RuntimeCommandError as error:
        message = str(error)
        if "already used by worktree" not in message and "already checked out" not in message:
            raise
        _cleanup_conflicting_worktree(project_dir=project_dir, worktree_dir=worktree_dir, branch_name=context.branch_name, error_text=message)
        run_or_raise(
            git_command,
            cwd=project_dir,
            max_retries=1,
            base_delay_seconds=0.5,
            max_delay_seconds=1.0,
        )

    _sync_task_markdown_to_worktree(
        project_dir=project_dir,
        worktree_dir=worktree_dir,
        task_path=Path(str(context.task.path)),
    )

    completed = _run_or_raise_with_progress(
        opencode_command,
        cwd=worktree_dir,
        max_retries=2,
        base_delay_seconds=1.0,
        max_delay_seconds=4.0,
        label=f"opencode:{context.task.task_id}",
    )

    commit_info = ""
    if auto_commit:
        commit_info = _auto_commit_worktree(worktree_dir, context.task.task_id, context.task.title)

    return {
        "worktree": str(worktree_dir),
        "dispatch": (completed.stdout.strip() + ("\n" + commit_info if commit_info else "")).strip(),
    }


def _worktree_root(project_dir: Path) -> Path:
    return project_dir / ".smartworkmate" / "worktrees"


def _sync_task_markdown_to_worktree(*, project_dir: Path, worktree_dir: Path, task_path: Path) -> None:
    try:
        relative = task_path.resolve().relative_to(project_dir.resolve())
    except Exception:
        return

    source = project_dir / relative
    target = worktree_dir / relative
    if not source.exists():
        return
    try:
        if source.resolve() == target.resolve():
            return
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _sync_task_file_for_acceptance(*, project_dir: Path, verify_root: Path, task_id: str) -> None:
    task = _find_task_by_id(project_dir, task_id)
    if task is None:
        return
    _sync_task_markdown_to_worktree(
        project_dir=project_dir,
        worktree_dir=verify_root,
        task_path=Path(str(task.path)),
    )


def _ensure_worktree_available(*, project_dir: Path, worktree_dir: Path, branch_name: str) -> None:
    if worktree_dir.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=project_dir,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=project_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=project_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _cleanup_conflicting_worktree(*, project_dir: Path, worktree_dir: Path, branch_name: str, error_text: str) -> None:
    conflict_path = _extract_conflict_worktree_path(error_text)
    if conflict_path:
        subprocess.run(
            ["git", "worktree", "remove", "--force", conflict_path],
            cwd=project_dir,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    _ensure_worktree_available(project_dir=project_dir, worktree_dir=worktree_dir, branch_name=branch_name)


def _extract_conflict_worktree_path(error_text: str) -> str:
    match = re.search(r"at '([^']+)'", error_text)
    if not match:
        return ""
    return match.group(1).strip()


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


def _run_or_raise_with_progress(
    command: list[str],
    *,
    cwd: Path,
    max_retries: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    label: str,
) -> Any:
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, Exception] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = run_or_raise(
                command,
                cwd=cwd,
                max_retries=max_retries,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
        except Exception as error:  # noqa: BLE001
            error_holder["error"] = error

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()

    elapsed = 0
    while thread.is_alive():
        if elapsed == 0 or elapsed % 10 == 0:
            print(f"[RUN] {label} running... {elapsed}s", flush=True)
        time.sleep(1)
        elapsed += 1

    thread.join()
    print(f"[RUN] {label} finished in {elapsed}s", flush=True)

    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder["result"]


def _status_for_failure_type(failure_type: str, *, execute: bool) -> str:
    if not execute:
        return TaskStatus.TODO.value
    if failure_type == NETWORK_FAILURE:
        return TaskStatus.TODO.value
    if failure_type in {PERMISSION_FAILURE, TASK_FORMAT_FAILURE, COMMAND_EXECUTION_FAILURE}:
        return TaskStatus.BLOCKED.value
    return TaskStatus.BLOCKED.value


def _validate_branch_ready_for_pr(project_dir: Path, *, base: str, branch: str) -> dict[str, str]:
    exists = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=project_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if exists.returncode != 0:
        return {
            "ok": "false",
            "failure_type": COMMAND_EXECUTION_FAILURE,
            "reason": f"branch {branch} not found for PR",
        }

    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{base}..{branch}"],
        cwd=project_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if ahead.returncode != 0:
        return {
            "ok": "false",
            "failure_type": COMMAND_EXECUTION_FAILURE,
            "reason": f"cannot compare commits for {branch} against {base}",
        }
    try:
        ahead_count = int(ahead.stdout.strip() or "0")
    except ValueError:
        ahead_count = 0
    if ahead_count <= 0:
        return {
            "ok": "false",
            "failure_type": COMMAND_EXECUTION_FAILURE,
            "reason": f"branch {branch} has no new commits; PR creation skipped",
        }

    return {"ok": "true"}


def _auto_commit_worktree(worktree_dir: Path, task_id: str, title: str) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if not status.stdout.strip():
        return "auto-commit: no changes detected"

    run_or_raise(
        ["git", "add", "-A"],
        cwd=worktree_dir,
        max_retries=1,
        base_delay_seconds=0.5,
        max_delay_seconds=1.0,
    )
    message = f"feat({task_id.lower()}): {title}"[:120]
    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=worktree_dir,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if commit.returncode != 0:
        return "auto-commit: skipped (commit command failed or no staged changes)"
    return "auto-commit: created commit"


def _load_execution_policy(project_dir: Path) -> ExecutionPolicy:
    config_path = project_dir / ".smartworkmate" / "config.yaml"
    if not config_path.exists():
        return ExecutionPolicy(
            backend="auto",
            require_worktree_isolation=True,
            auto_commit=True,
        )
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}

    backend = str(data.get("execution_backend", "auto")).strip().lower()
    if backend not in {"kimaki", "opencode_local", "auto"}:
        backend = "auto"
    return ExecutionPolicy(
        backend=backend,
        require_worktree_isolation=bool(data.get("require_worktree_isolation", True)),
        auto_commit=bool(data.get("auto_commit", True)),
    )


def _should_use_kimaki_backend(
    *,
    backend: str,
    has_channel: bool,
    kimaki_available: bool,
    require_worktree_isolation: bool,
) -> bool:
    if require_worktree_isolation:
        return False

    mode = (backend or "").strip().lower()
    if mode == "kimaki":
        return has_channel and kimaki_available
    if mode == "opencode_local":
        return False
    if mode == "auto":
        return has_channel and kimaki_available
    return False


def _render_live_status(
    *,
    cycle_index: int,
    cycle_result: dict[str, Any],
    root: Path,
    execute: bool,
    interval_seconds: int,
    seconds_to_next: int,
) -> None:
    processed = cycle_result.get("processed", [])
    if not isinstance(processed, list):
        processed = []

    active_lines: list[str] = []
    dispatch_lines: list[str] = []
    auto_lines: list[str] = []
    pr_lines: list[str] = []
    failure_lines: list[str] = []
    policy_lines: list[str] = []
    project_set: set[str] = set()

    status_counts = {
        "dispatch": 0,
        "active": 0,
        "auto": 0,
        "pr": 0,
        "failure": 0,
    }

    for item in processed:
        if not isinstance(item, dict):
            continue
        project = str(item.get("project", ""))
        short_project = _project_label(project)
        project_set.add(short_project)

        policy = item.get("policy")
        if isinstance(policy, dict):
            policy_lines.append(
                (
                    f"- {short_project}: backend={policy.get('backend', '')}, "
                    f"worktree={policy.get('require_worktree_isolation', '')}, "
                    f"auto_commit={policy.get('auto_commit', '')}"
                )
            )

        if item.get("result") == "waiting_active_tasks":
            ids = item.get("active_task_ids", [])
            if isinstance(ids, list) and ids:
                active_lines.append(f"- {short_project}: active {', '.join(str(x) for x in ids)}")
                status_counts["active"] += len(ids)

        mode = str(item.get("mode", ""))
        if mode in {"kimaki", "opencode"}:
            dispatch_lines.append(
                f"- {short_project}: {mode} -> {item.get('task_id', '')} ({item.get('run_id', '')})"
            )
            status_counts["dispatch"] += 1

        if mode == "idle_task":
            result = item.get("result", {})
            if isinstance(result, dict):
                auto_lines.append(
                    f"- {short_project}: 自动任务 {result.get('result', '')} {result.get('task_id', '')}".strip()
                )
                status_counts["auto"] += 1

        if mode == "reconcile":
            events = item.get("events", [])
            if isinstance(events, list):
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    task_id = str(event.get("task_id", ""))
                    sync = event.get("sync", {})
                    auto_pr = event.get("auto_pr", {})
                    if isinstance(sync, dict) and sync.get("pr_url"):
                        pr_lines.append(f"- {short_project}: {task_id} PR {sync.get('pr_url', '')}")
                        status_counts["pr"] += 1
                    if isinstance(auto_pr, dict) and auto_pr.get("url"):
                        pr_lines.append(f"- {short_project}: {task_id} PR {auto_pr.get('url', '')}")
                        status_counts["pr"] += 1
                    if isinstance(auto_pr, dict) and auto_pr.get("reason"):
                        pr_lines.append(
                            f"- {short_project}: {task_id} PR blocked ({auto_pr.get('reason', '')})"
                        )
                        failure_lines.append(
                            f"- {short_project}: {task_id} PR blocked: {auto_pr.get('reason', '')}"
                        )
                        status_counts["failure"] += 1

        result = item.get("result")
        if isinstance(result, dict):
            reason = result.get("reason")
            if reason:
                failure_lines.append(f"- {short_project}: {mode or 'unknown'} failed: {reason}")
                status_counts["failure"] += 1

    mode_text = "执行模式" if execute else "干跑模式"
    countdown_text = f"{seconds_to_next}s" if seconds_to_next > 0 else "立即"
    now_text = datetime.now().isoformat(timespec="seconds")

    _append_history(
        now_text,
        dispatch_lines=dispatch_lines,
        active_lines=active_lines,
        auto_lines=auto_lines,
        pr_lines=pr_lines,
        failure_lines=failure_lines,
    )

    palette = _color_palette()
    title = _paint("SmartWorkmate 实时固定面板", palette["title"])
    dim = palette["dim"]

    buffer_lines: list[str] = []
    buffer_lines.append(title)
    buffer_lines.append(_paint("=" * 86, palette["rule"]))
    buffer_lines.append(f"时间: {now_text}")
    buffer_lines.append(f"根目录: {root}")
    buffer_lines.append(
        f"模式: {mode_text} | 周期: {cycle_index} | 目标项目: {cycle_result.get('targets', 0)} | 识别项目: {len(project_set)}"
    )
    buffer_lines.append(f"轮询间隔: {interval_seconds}s | 下次刷新: {countdown_text} | 退出: Ctrl+C")
    buffer_lines.append(
        "统计: "
        f"派发={status_counts['dispatch']} | 活跃={status_counts['active']} | "
        f"自动任务={status_counts['auto']} | PR事件={status_counts['pr']} | 异常={status_counts['failure']}"
    )
    buffer_lines.append("")

    buffer_lines.append(_paint("[当前派发]", palette["section"]))
    buffer_lines.extend(_limit_lines(dispatch_lines, default_line="- 无"))

    buffer_lines.append("")
    buffer_lines.append(_paint("[执行中的任务]", palette["section"]))
    buffer_lines.extend(_limit_lines(active_lines, default_line="- 无"))

    buffer_lines.append("")
    buffer_lines.append(_paint("[自动发现与任务生成]", palette["section"]))
    buffer_lines.extend(_limit_lines(auto_lines, default_line="- 无"))

    buffer_lines.append("")
    buffer_lines.append(_paint("[PR 状态]", palette["section"]))
    buffer_lines.extend(_limit_lines(pr_lines, default_line="- 无"))

    buffer_lines.append("")
    buffer_lines.append(_paint("[执行策略]", palette["section"]))
    buffer_lines.extend(_limit_lines(policy_lines, default_line="- 无策略信息"))

    buffer_lines.append("")
    failure_header_color = palette["error"] if failure_lines else palette["section"]
    buffer_lines.append(_paint("[异常与阻塞]", failure_header_color))
    buffer_lines.extend(_limit_lines(failure_lines, default_line="- 无"))

    buffer_lines.append("")
    buffer_lines.append(_paint("[历史日志]", palette["section"]))
    history_lines = list(_LIVE_HISTORY)
    if history_lines:
        buffer_lines.extend(history_lines[-18:])
    else:
        buffer_lines.append("- 暂无历史事件")

    buffer_lines.append("")
    buffer_lines.append(f"{dim}提示: 固定面板每秒刷新; 历史日志保留最近 {LIVE_HISTORY_LIMIT} 条事件。{_Color.RESET}")

    screen = "\n".join(buffer_lines)
    os.system("cls" if os.name == "nt" else "clear")
    print(screen, end="\n", flush=True)


def _append_history(
    timestamp: str,
    *,
    dispatch_lines: list[str],
    active_lines: list[str],
    auto_lines: list[str],
    pr_lines: list[str],
    failure_lines: list[str],
) -> None:
    for line in dispatch_lines[:4]:
        _LIVE_HISTORY.append(f"{timestamp} [dispatch] {line}")
    for line in active_lines[:4]:
        _LIVE_HISTORY.append(f"{timestamp} [active] {line}")
    for line in auto_lines[:4]:
        _LIVE_HISTORY.append(f"{timestamp} [auto] {line}")
    for line in pr_lines[:4]:
        _LIVE_HISTORY.append(f"{timestamp} [pr] {line}")
    for line in failure_lines[:6]:
        _LIVE_HISTORY.append(f"{timestamp} [error] {line}")


def _limit_lines(lines: list[str], *, default_line: str, limit: int = 12) -> list[str]:
    if not lines:
        return [default_line]
    if len(lines) <= limit:
        return lines
    omitted = len(lines) - limit
    return lines[:limit] + [f"- ... 还有 {omitted} 条"]


def _color_palette() -> dict[str, str]:
    if not _supports_ansi_color():
        return {
            "title": "",
            "section": "",
            "rule": "",
            "error": "",
            "dim": "",
        }
    return {
        "title": _Color.BOLD + _Color.CYAN,
        "section": _Color.BOLD + _Color.BLUE,
        "rule": _Color.MAGENTA,
        "error": _Color.BOLD + _Color.RED,
        "dim": _Color.DIM + _Color.YELLOW,
    }


def _paint(text: str, style: str) -> str:
    if not style:
        return text
    return f"{style}{text}{_Color.RESET}"


def _supports_ansi_color() -> bool:
    if not sys.stdout.isatty():
        return False
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf-8" not in encoding and "utf8" not in encoding:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.name != "nt":
        return True
    term = os.environ.get("TERM", "").lower()
    if "xterm" in term or "ansi" in term:
        return True
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("ANSICON"):
        return True
    if os.environ.get("ConEmuANSI", "").upper() == "ON":
        return True
    return False


def _configure_console_output_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if os.name != "nt":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass


def _project_label(project_path: str) -> str:
    path = Path(project_path)
    name = path.name or project_path
    parent = path.parent.name if path.parent else ""
    label = f"{name} ({parent})" if parent else name
    if len(label) <= 64:
        return label
    return label[:61] + "..."


def _sleep_with_heartbeat(
    seconds: int,
    *,
    enabled: bool,
    on_tick: Any,
) -> None:
    if seconds <= 0:
        return
    if not enabled:
        time.sleep(seconds)
        return

    remaining = seconds
    while remaining > 0:
        on_tick(remaining)
        time.sleep(1)
        remaining -= 1
    on_tick(0)


def _extract_task_id_from_text(text: str) -> str:
    match = re.search(r"(TSK-\d{4}-\d{3}|AUTO-[0-9A-Fa-f]{8})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).upper()


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


def _maybe_gh_bin() -> str:
    binary = shutil.which("gh")
    if binary:
        return binary
    candidates = [
        Path("C:/Program Files/GitHub CLI/gh.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "GitHub CLI" / "gh.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""
