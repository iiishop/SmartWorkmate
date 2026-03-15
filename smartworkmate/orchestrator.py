from __future__ import annotations

import json
import re
import shutil
import shlex
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import RunContext, Task, TaskStatus
from .state_store import StateStore
from .task_loader import load_tasks


PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PR_URL_RE = re.compile(r"https://github\.com/[^\s)]+/pull/\d+")


def select_next_task(tasks: list[Task]) -> Task | None:
    candidates = [task for task in tasks if task.status in {TaskStatus.TODO, TaskStatus.REWORK}]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda task: (
            PRIORITY_ORDER.get(task.priority.lower(), 99),
            task.task_id,
        ),
    )[0]


def build_run_context(task: Task, dry_run: bool) -> RunContext:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"run-{task.task_id.lower()}-{stamp}"
    suffix = _slugify(task.title)
    worktree_name = f"{task.task_id.lower()}-{suffix}"[:48]
    branch_name = f"task/{task.task_id.lower()}-{suffix}"[:64]
    thread_name = f"{task.task_id} | {task.title}"[:90]

    prompt = _build_kimaki_prompt(task, branch_name=branch_name)
    return RunContext(
        run_id=run_id,
        task=task,
        worktree_name=worktree_name,
        branch_name=branch_name,
        thread_name=thread_name,
        prompt=prompt,
        dry_run=dry_run,
        metadata={"created_at": datetime.now(timezone.utc).isoformat()},
    )


def write_run_context(repo_root: Path, context: RunContext) -> Path:
    runs_dir = repo_root / ".smartworkmate" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": context.run_id,
        "task": asdict(context.task),
        "worktree_name": context.worktree_name,
        "branch_name": context.branch_name,
        "thread_name": context.thread_name,
        "prompt": context.prompt,
        "dry_run": context.dry_run,
        "metadata": context.metadata,
    }
    payload["task"]["path"] = str(context.task.path)
    out = runs_dir / f"{context.run_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def dispatch_with_kimaki(
    *,
    repo_root: Path,
    context: RunContext,
    config: dict[str, object],
) -> str:
    channel = str(config["channel_id"])
    user = str(config.get("user", "iiishop"))
    command = [
        _resolve_kimaki_bin(),
        "send",
        "--channel",
        channel,
        "--prompt",
        context.prompt,
        "--name",
        context.thread_name,
        "--worktree",
        context.worktree_name,
        "--user",
        user,
    ]

    if context.dry_run:
        return "DRY-RUN: " + " ".join(shlex.quote(part) for part in command)

    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def run_once(repo_root: Path, execute: bool) -> dict[str, str]:
    config = _load_config(repo_root)
    tasks = load_tasks(repo_root / "docs" / "tasks")
    task = select_next_task(tasks)
    if task is None:
        return {"result": "No TODO/REWORK tasks found"}

    context = build_run_context(task, dry_run=not execute)
    context_path = write_run_context(repo_root, context)

    store = StateStore(repo_root / ".smartworkmate" / "state.json")
    state = store.load()
    next_status = TaskStatus.IN_PROGRESS.value if execute else TaskStatus.TODO.value
    store.upsert_task(
        state,
        task_id=task.task_id,
        status=next_status,
        base_branch=task.base_branch,
        run_id=context.run_id,
        branch_name=context.branch_name,
        worktree_name=context.worktree_name,
    )
    store.save(state)

    dispatch_output = dispatch_with_kimaki(
        repo_root=repo_root,
        context=context,
        config=config,
    )

    session_id = ""
    thread_id = ""
    if execute:
        session_id, thread_id = _detect_latest_kimaki_session(repo_root, task.task_id)
        if session_id or thread_id:
            store.upsert_task(
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
            store.save(state)

    return {
        "result": "Dispatched",
        "task_id": task.task_id,
        "run_id": context.run_id,
        "context": str(context_path),
        "thread_name": context.thread_name,
        "session_id": session_id,
        "thread_id": thread_id,
        "dispatch": dispatch_output,
    }


def update_task_state(
    repo_root: Path,
    *,
    task_id: str,
    status: str,
    pr_url: str,
    notes: str,
) -> dict[str, str]:
    store = StateStore(repo_root / ".smartworkmate" / "state.json")
    state = store.load()
    updated = store.update_task_status(
        state,
        task_id=task_id,
        status=status,
        pr_url=pr_url,
        notes=notes,
    )
    store.save(state)
    return {
        "result": "updated",
        "task_id": task_id,
        "status": updated.status,
        "pr_url": updated.pr_url,
        "approved_by": updated.approved_by,
        "approved_at": updated.approved_at,
        "thread_id": updated.thread_id,
        "session_id": updated.session_id,
        "updated_at": updated.updated_at,
    }


def approve_task(repo_root: Path, *, task_id: str, approver: str) -> dict[str, str]:
    store = StateStore(repo_root / ".smartworkmate" / "state.json")
    state = store.load()
    updated = store.set_task_approval(state, task_id=task_id, approved_by=approver)
    store.save(state)
    return {
        "result": "approved",
        "task_id": task_id,
        "approved_by": updated.approved_by,
        "approved_at": updated.approved_at,
        "status": updated.status,
        "pr_url": updated.pr_url,
    }


def sync_task_from_kimaki(repo_root: Path, *, task_id: str) -> dict[str, str]:
    store = StateStore(repo_root / ".smartworkmate" / "state.json")
    state = store.load()
    record = state.tasks.get(task_id)
    if record is None:
        raise KeyError(f"Task {task_id} not found in state")

    session_id = record.session_id
    if not session_id:
        session_id, thread_id = _detect_latest_kimaki_session(repo_root, task_id)
        if session_id:
            store.upsert_task(
                state,
                task_id=task_id,
                status=record.status,
                base_branch=record.base_branch,
                run_id=record.last_run_id,
                branch_name=record.branch_name,
                worktree_name=record.worktree_name,
                session_id=session_id,
                thread_id=thread_id,
            )
            store.save(state)
            record = state.tasks[task_id]

    if not session_id:
        return {
            "result": "no_session",
            "task_id": task_id,
            "status": record.status,
            "pr_url": record.pr_url,
            "session_id": "",
            "thread_id": record.thread_id,
        }

    conversation = _read_session_markdown(repo_root, session_id)
    pr_url = _extract_latest_pr_url(conversation)
    if not pr_url:
        return {
            "result": "no_pr_url",
            "task_id": task_id,
            "status": record.status,
            "pr_url": record.pr_url,
            "session_id": session_id,
            "thread_id": record.thread_id,
        }

    updated = store.update_task_status(
        state,
        task_id=task_id,
        status=TaskStatus.PR_OPEN.value,
        pr_url=pr_url,
        notes="synced from kimaki session",
    )
    store.save(state)
    return {
        "result": "synced",
        "task_id": task_id,
        "status": updated.status,
        "pr_url": updated.pr_url,
        "session_id": updated.session_id,
        "thread_id": updated.thread_id,
        "updated_at": updated.updated_at,
    }


def _build_kimaki_prompt(task: Task, *, branch_name: str) -> str:
    acceptance = "\n".join(f"- {item}" for item in task.acceptance_checks)
    refs = "\n".join(f"- {item}" for item in task.references) if task.references else "- (none)"
    return (
        f"任务ID: {task.task_id}\n"
        f"标题: {task.title}\n"
        f"目标分支: {task.base_branch}\n"
        f"工作分支: {branch_name}\n\n"
        "请你像真实程序员一样执行该任务：\n"
        "1) 先分析并输出实现计划\n"
        "2) 在当前worktree中实现，建议多次commit\n"
        "3) 严格执行验收项\n"
        "4) 完成后创建PR并在描述中映射验收结果\n\n"
        "任务需求:\n"
        f"{task.requirements}\n\n"
        "任务设计:\n"
        f"{task.design}\n\n"
        "验收标准:\n"
        f"{acceptance}\n\n"
        "引用文件:\n"
        f"{refs}\n"
    )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "task"


def _detect_latest_kimaki_session(repo_root: Path, task_id: str) -> tuple[str, str]:
    command = [
        _resolve_kimaki_bin(),
        "session",
        "list",
        "--json",
        "--project",
        str(repo_root),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    try:
        payload = _extract_json_payload(completed.stdout)
    except RuntimeError:
        return "", ""
    data = json.loads(payload)
    if not isinstance(data, list):
        return "", ""

    candidates = [
        item
        for item in data
        if isinstance(item, dict)
        and str(item.get("source", "")) == "kimaki"
        and (
            task_id in str(item.get("title", ""))
            or str(item.get("threadId", ""))
        )
    ]
    if not candidates:
        return "", ""
    latest = candidates[0]
    return str(latest.get("id", "")), str(latest.get("threadId", ""))


def _extract_json_payload(stdout: str) -> str:
    clean = ANSI_ESCAPE_RE.sub("", stdout)
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("[") or stripped.startswith("{")):
            continue
        candidate = "\n".join(lines[index:]).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    raise RuntimeError("Unable to parse JSON from kimaki output")


def _read_session_markdown(repo_root: Path, session_id: str) -> str:
    command = [
        _resolve_kimaki_bin(),
        "session",
        "read",
        session_id,
        "--project",
        str(repo_root),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return completed.stdout


def _extract_latest_pr_url(conversation_markdown: str) -> str:
    matches = PR_URL_RE.findall(conversation_markdown)
    if not matches:
        return ""
    return matches[-1]


def _resolve_kimaki_bin() -> str:
    binary = shutil.which("kimaki")
    if binary:
        return binary
    fallback = Path.home() / ".kimaki" / "bin" / "kimaki.CMD"
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError("kimaki executable not found")


def _load_config(repo_root: Path) -> dict[str, object]:
    config_path = repo_root / ".smartworkmate" / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            "Missing .smartworkmate/config.yaml. Copy from .smartworkmate/config.example.yaml"
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if "channel_id" not in data:
        raise ValueError("config.yaml must include channel_id")
    return data
