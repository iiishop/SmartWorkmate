from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBUI_ROOT = PROJECT_ROOT / "webui"
DIST_ROOT = WEBUI_ROOT / "dist"
INDEX_HTML = DIST_ROOT / "index.html"


class StartRequest(BaseModel):
    mode: str = Field(default="execute_daemon")
    user: str = Field(default="iiishop")
    root: str = Field(default="")
    interval: int = Field(default=300, ge=30)
    opencode_global: bool = Field(default=True)


@dataclass(slots=True)
class RuntimeState:
    running: bool = False
    mode: str = "execute_daemon"
    user: str = "iiishop"
    root: str = ""
    interval: int = 300
    opencode_global: bool = True
    execute: bool = True
    cycle: int = 0
    next_run_at: float = 0.0
    last_updated: str = ""
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "dispatch": 0,
            "active": 0,
            "auto": 0,
            "pr": 0,
            "error": 0,
            "git_sync": 0,
        }
    )
    dispatch: list[str] = field(default_factory=list)
    active: list[str] = field(default_factory=list)
    auto: list[str] = field(default_factory=list)
    pr: list[str] = field(default_factory=list)
    pr_tracking: list[str] = field(default_factory=list)
    pr_breakdown: dict[str, int] = field(
        default_factory=lambda: {"open": 0, "merged": 0, "rejected": 0, "followup": 0}
    )
    git_sync: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    projects: dict[str, dict[str, int]] = field(default_factory=dict)
    history: deque[str] = field(default_factory=lambda: deque(maxlen=200))
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    last_error: str = ""


app = FastAPI(title="SmartWorkmate Web UI")
_lock = threading.RLock()
_stop_event = threading.Event()
_worker: threading.Thread | None = None
_state = RuntimeState()

if (DIST_ROOT / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST_ROOT / "assets"), name="assets")


def _append_log(line: str) -> None:
    with _lock:
        _state.logs.append(line)


def _append_history(lines: list[str]) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        for line in lines[:14]:
            _state.history.append(f"{stamp} {line}")


def _parse_json_payload(text: str) -> dict[str, Any]:
    clean = ANSI_ESCAPE_RE.sub("", text or "")
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            continue
        candidate = "\n".join(lines[index:]).strip()
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else {"payload": value}
        except json.JSONDecodeError:
            continue
    return {}


def _build_command() -> list[str]:
    with _lock:
        execute = _state.execute
        mode = _state.mode
        user = _state.user
        interval = _state.interval
        root = _state.root
        opencode_global = _state.opencode_global

    command = [
        "uv",
        "run",
        "python",
        "-m",
        "smartworkmate.cli",
        "start",
        "--once",
        "--no-live",
        "--user",
        user,
        "--interval",
        str(interval),
    ]
    if root.strip():
        command.extend(["--root", root.strip()])
    command.append("--execute" if execute else "--dry-run")
    if opencode_global:
        command.append("--opencode-global")
    if mode == "dry_run_once":
        command = [item for item in command if item != "--execute"]
        if "--dry-run" not in command:
            command.append("--dry-run")
    return command


def _run_cycle() -> int:
    command = _build_command()
    _append_log(f"$ {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    if completed.stdout.strip():
        for line in completed.stdout.splitlines()[-40:]:
            _append_log(line)
    if completed.stderr.strip():
        for line in completed.stderr.splitlines()[-40:]:
            _append_log(f"[stderr] {line}")

    payload = _parse_json_payload(completed.stdout or "")
    if payload:
        _apply_payload(payload)
    else:
        _append_log("[WARN] 未解析到结构化 JSON 输出")

    _append_log(f"[exit={completed.returncode}] {' '.join(command)}")
    return int(completed.returncode)


def _apply_payload(payload: dict[str, Any]) -> None:
    cycles = payload.get("cycles", [])
    if not isinstance(cycles, list) or not cycles:
        return

    latest = cycles[-1]
    processed = latest.get("processed", []) if isinstance(latest, dict) else []
    if not isinstance(processed, list):
        processed = []

    dispatch: list[str] = []
    active: list[str] = []
    auto: list[str] = []
    pr: list[str] = []
    pr_tracking: list[str] = []
    git_sync: list[str] = []
    errors: list[str] = []
    policies: list[str] = []

    stats = {"dispatch": 0, "active": 0, "auto": 0, "pr": 0, "error": 0, "git_sync": 0}
    pr_breakdown = {"open": 0, "merged": 0, "rejected": 0, "followup": 0}
    projects: dict[str, dict[str, int]] = {}

    for item in processed:
        if not isinstance(item, dict):
            continue

        project = str(item.get("project", ""))
        name = Path(project).name or project
        projects.setdefault(name, {"dispatch": 0, "active": 0, "auto": 0, "error": 0, "sync": 0})

        mode = str(item.get("mode", ""))
        if mode == "git_sync":
            result = item.get("result", {})
            if isinstance(result, dict):
                status = str(result.get("result", ""))
                text = str(result.get("message") or result.get("error") or "")
                git_sync.append(f"- {name}: {status} {text}".strip())
                stats["git_sync"] += 1
                projects[name]["sync"] += 1

        if mode in {"kimaki", "opencode"}:
            dispatch.append(f"- {name}: {mode} -> {item.get('task_id', '')}")
            stats["dispatch"] += 1
            projects[name]["dispatch"] += 1

        if item.get("result") == "waiting_active_tasks":
            ids = item.get("active_task_ids", [])
            if isinstance(ids, list) and ids:
                active.append(f"- {name}: {', '.join(str(x) for x in ids)}")
                stats["active"] += len(ids)
                projects[name]["active"] += len(ids)

        if mode == "idle_task":
            result = item.get("result", {})
            if isinstance(result, dict):
                auto.append(f"- {name}: 自动任务 {result.get('result', '')}")
                stats["auto"] += 1
                projects[name]["auto"] += 1

        if mode == "reconcile":
            events = item.get("events", [])
            if isinstance(events, list):
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    task_id = str(event.get("task_id", ""))
                    auto_pr = event.get("auto_pr")
                    if isinstance(auto_pr, dict) and auto_pr.get("url"):
                        pr.append(f"- {name}: {task_id} PR {auto_pr.get('url', '')}")
                        stats["pr"] += 1
                    elif isinstance(auto_pr, dict) and auto_pr.get("reason"):
                        reason = str(auto_pr.get("reason", ""))
                        pr.append(f"- {name}: {task_id} PR 阻塞: {reason}")
                        errors.append(f"- {name}: {reason}")
                        stats["error"] += 1
                        projects[name]["error"] += 1

                    pr_track = event.get("pr_track")
                    if isinstance(pr_track, dict):
                        status = str(pr_track.get("status", "")).lower().strip()
                        pr_url = str(pr_track.get("pr_url", ""))
                        if status == "open":
                            pr_breakdown["open"] += 1
                            pr_tracking.append(f"- {name}: {task_id} PR 仍打开 {pr_url}")
                        elif status == "merged":
                            pr_breakdown["merged"] += 1
                            pr_tracking.append(f"- {name}: {task_id} PR 已合并 {pr_url}")
                        elif status == "closed_unmerged":
                            pr_breakdown["rejected"] += 1
                            pr_tracking.append(f"- {name}: {task_id} PR 已拒绝/关闭未合并 {pr_url}")

                    pr_followup = event.get("pr_rejection_followup")
                    if isinstance(pr_followup, dict):
                        followup_task_id = str(pr_followup.get("followup_task_id", "")).strip()
                        if followup_task_id:
                            pr_breakdown["followup"] += 1
                            pr_tracking.append(f"- {name}: {task_id} 已创建后续处理任务 {followup_task_id}")

        policy = item.get("policy")
        if isinstance(policy, dict):
            policies.append(
                f"- {name}: backend={policy.get('backend', '')}, worktree={policy.get('require_worktree_isolation', '')}, auto_commit={policy.get('auto_commit', '')}"
            )

        result = item.get("result")
        if isinstance(result, dict) and result.get("reason"):
            reason = str(result.get("reason", ""))
            errors.append(f"- {name}: {reason}")
            stats["error"] += 1
            projects[name]["error"] += 1

    _append_history(dispatch + active + auto + pr + pr_tracking + git_sync + errors)
    with _lock:
        _state.cycle += 1
        _state.dispatch = dispatch
        _state.active = active
        _state.auto = auto
        _state.pr = pr
        _state.pr_tracking = pr_tracking
        _state.pr_breakdown = pr_breakdown
        _state.git_sync = git_sync
        _state.errors = errors
        _state.policies = policies
        _state.stats = stats
        _state.projects = projects
        _state.last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _worker_loop() -> None:
    while not _stop_event.is_set():
        with _lock:
            mode = _state.mode
            interval = _state.interval

        code = _run_cycle()
        with _lock:
            _state.last_error = "" if code == 0 else f"cycle failed with code={code}"

        if mode != "execute_daemon":
            break

        for elapsed in range(interval):
            if _stop_event.is_set():
                break
            with _lock:
                _state.next_run_at = time.time() + (interval - elapsed)
            time.sleep(1)

    with _lock:
        _state.running = False
        _state.next_run_at = 0.0


@app.get("/")
def index() -> Any:
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return JSONResponse(
        {
            "error": "webui build artifacts not found",
            "hint": "Run npm install && npm run build in webui/",
        },
        status_code=500,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ws")
def websocket_probe() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_status(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"status": "ok", "message": "SmartWorkmate websocket endpoint"})
    await websocket.close()


@app.get("/api/state")
def state() -> dict[str, Any]:
    with _lock:
        next_run_in = 0
        if _state.running and _state.mode == "execute_daemon" and _state.next_run_at > time.time():
            next_run_in = int(_state.next_run_at - time.time())

        return {
            "running": _state.running,
            "mode": _state.mode,
            "user": _state.user,
            "root": _state.root,
            "interval": _state.interval,
            "opencode_global": _state.opencode_global,
            "cycle": _state.cycle,
            "next_run_in": next_run_in,
            "last_updated": _state.last_updated,
            "stats": _state.stats,
            "dispatch": list(_state.dispatch),
            "active": list(_state.active),
            "auto": list(_state.auto),
            "pr": list(_state.pr),
            "pr_tracking": list(_state.pr_tracking),
            "pr_breakdown": _state.pr_breakdown,
            "git_sync": list(_state.git_sync),
            "errors": list(_state.errors),
            "policies": list(_state.policies),
            "projects": _state.projects,
            "history": list(_state.history),
            "logs": list(_state.logs),
            "last_error": _state.last_error,
        }


@app.post("/api/start")
def start(req: StartRequest) -> dict[str, Any]:
    global _worker
    with _lock:
        if _state.running:
            raise HTTPException(status_code=409, detail="already running")

        _state.running = True
        _state.mode = req.mode
        _state.user = req.user.strip() or "iiishop"
        _state.root = req.root.strip()
        _state.interval = max(30, int(req.interval))
        _state.opencode_global = bool(req.opencode_global)
        _state.execute = req.mode != "dry_run_once"
        _state.last_error = ""

    _stop_event.clear()
    _worker = threading.Thread(target=_worker_loop, daemon=True)
    _worker.start()
    return {"result": "started", "mode": req.mode}


@app.post("/api/stop")
def stop() -> dict[str, Any]:
    _stop_event.set()
    return {"result": "stopping"}
