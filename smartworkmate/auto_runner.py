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

from .acceptance import evaluate_task_acceptance
from .models import TaskStatus
from .orchestrator import (
    _detect_latest_kimaki_session,
    _resolve_kimaki_bin,
    build_run_context,
    select_next_task,
    sync_task_from_kimaki,
    write_run_context,
)
from .state_store import StateStore, TaskRecord
from .status_sync import sync_state_and_tasks
from .task_loader import TaskFormatError, load_tasks
import yaml
from .proactive import create_idle_improvement_task, refresh_project_memory


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

    for directory in _opencode_project_roots(root):
        key = str(directory)
        targets_by_dir[key] = ProjectTarget(directory=directory)

    kimaki_bin = _maybe_kimaki_bin()
    if kimaki_bin:
        kimaki_projects = _safe_json_command([kimaki_bin, "project", "list", "--json"], cwd=root)
        if isinstance(kimaki_projects, list):
            for item in kimaki_projects:
                if not isinstance(item, dict):
                    continue
                directory = Path(str(item.get("directory", ""))).resolve()
                key = str(directory)
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


def _opencode_project_roots(scope_root: Path) -> list[Path]:
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
        directory = Path(raw).resolve()
        directory_key = str(directory)
        if directory_key in seen:
            continue
        if not _is_same_or_child(scope, directory):
            continue
        seen.add(directory_key)
        roots.append(directory)

    return roots


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

        sync_result = sync_state_and_tasks(project_dir)
        processed.append(
            {
                "project": str(project_dir),
                "mode": "state_markdown_sync",
                "result": sync_result,
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
                }
            )
        if reconciliation["active_task_ids"]:
            processed.append(
                {
                    "project": str(project_dir),
                    "result": "waiting_active_tasks",
                    "active_task_ids": reconciliation["active_task_ids"],
                }
            )
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

        context = build_run_context(task, dry_run=not execute, repo_root=project_dir)
        context_path = write_run_context(project_dir, context)

        state_store = StateStore(project_dir / ".smartworkmate" / "state.json")
        state = state_store.load()
        state_store.upsert_task(
            state,
            task_id=task.task_id,
            status=TaskStatus.IN_PROGRESS.value if execute else TaskStatus.TODO.value,
            base_branch=task.base_branch,
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
                        base_branch=task.base_branch,
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
                    "acceptance": "pending_async_session_completion",
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
                    )
                    state_store.save(state)

            processed.append(
                {
                    "project": str(project_dir),
                    "task_id": task.task_id,
                    "mode": "opencode",
                    "run_id": context.run_id,
                    "context": str(context_path),
                    "worktree": dispatch_output.get("worktree", ""),
                    "dispatch": dispatch_output.get("dispatch", ""),
                    "acceptance": acceptance_summary,
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

        if refreshed and refreshed.pr_url:
            verify_root = _resolve_verification_root(project_dir, refreshed)
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

    conventional = project_dir.parent / f".{project_dir.name}-worktrees" / record.worktree_name
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

    existing = _gh_pr_view(project_dir, branch)
    if existing:
        return {"result": "exists", "url": existing}

    push_result = _push_branch(project_dir, branch)
    if push_result.get("result") != "ok":
        return {
            "result": "push_failed",
            "reason": push_result.get("reason", "unknown"),
        }

    title = f"[{record.task_id}] Automated task implementation"
    body = (
        "## Summary\n"
        "- Auto-created by SmartWorkmate reconcile loop\n"
        "- Task tracked in docs/tasks with acceptance checks\n"
    )
    create_result = _gh_pr_create(project_dir, base=base, head=branch, title=title, body=body)
    if create_result.get("url"):
        return {"result": "created", "url": str(create_result["url"])}
    return {
        "result": "create_failed",
        "reason": create_result.get("reason", "unknown"),
    }


def _push_branch(project_dir: Path, branch: str) -> dict[str, str]:
    try:
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=project_dir,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        return {"result": "ok"}
    except subprocess.CalledProcessError as error:
        return {
            "result": "error",
            "reason": (error.stderr or error.stdout or "push failed").strip(),
        }


def _gh_pr_view(project_dir: Path, branch: str) -> str:
    try:
        completed = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "url"],
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
    try:
        completed = subprocess.run(
            ["gh", "pr", "create", "--base", base, "--head", head, "--title", title, "--body", body],
            cwd=project_dir,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        return {
            "reason": (error.stderr or error.stdout or "gh pr create failed").strip(),
        }
    output = completed.stdout.strip()
    for line in output.splitlines():
        if line.startswith("https://"):
            return {"url": line.strip()}
    return {"reason": output or "missing PR URL in gh output"}


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
