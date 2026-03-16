from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(slots=True)
class SetupReport:
    config_path: Path
    created: bool
    detected: dict[str, Any]


def setup_auto(repo_root: Path, *, force: bool = False) -> SetupReport:
    config_dir = repo_root / ".smartworkmate"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    if config_path.exists() and not force:
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return SetupReport(config_path=config_path, created=False, detected={"config": existing})

    detected = _detect_environment(repo_root)
    config = {
        "channel_id": detected["channel_id"],
        "user": detected["user"],
        "project_directory": str(repo_root),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "detected_via": "smartworkmate setup --auto",
        "execution_backend": "auto",
        "require_worktree_isolation": True,
        "auto_commit": True,
        "manual_approval_required": False,
    }
    if detected.get("default_session_id"):
        config["default_session_id"] = detected["default_session_id"]
    if detected.get("default_thread_id"):
        config["default_thread_id"] = detected["default_thread_id"]

    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return SetupReport(config_path=config_path, created=True, detected=detected)


def _detect_environment(repo_root: Path) -> dict[str, Any]:
    kimaki_bin = shutil.which("kimaki")
    if kimaki_bin is None:
        raise RuntimeError("kimaki CLI not found in PATH")

    opencode_bin = shutil.which("opencode")
    projects = _run_json([kimaki_bin, "project", "list", "--json"], cwd=repo_root)
    if not isinstance(projects, list):
        raise RuntimeError("kimaki project list did not return JSON array")

    best = _select_project_mapping(projects, repo_root)
    if best is None:
        raise RuntimeError(
            "No kimaki project mapping found for this repo. Run `kimaki project add` first."
        )

    sessions = _run_json(
        [kimaki_bin, "session", "list", "--json", "--project", str(best["directory"])],
        cwd=repo_root,
    )

    latest_session_id = ""
    latest_thread_id = ""
    if isinstance(sessions, list):
        kimaki_sessions = [
            item
            for item in sessions
            if isinstance(item, dict) and str(item.get("source", "")) == "kimaki"
        ]
        if kimaki_sessions:
            latest = kimaki_sessions[0]
            latest_session_id = str(latest.get("id", ""))
            latest_thread_id = str(latest.get("threadId", ""))

    return {
        "kimaki_bin": kimaki_bin,
        "opencode_bin": opencode_bin or "",
        "channel_id": str(best["channel_id"]),
        "channel_name": str(best.get("channel_name", "")),
        "mapped_directory": str(best["directory"]),
        "user": os.environ.get("KIMAKI_DEFAULT_USER") or os.environ.get("USERNAME") or "iiishop",
        "default_session_id": latest_session_id,
        "default_thread_id": latest_thread_id,
    }


def _run_json(command: list[str], *, cwd: Path) -> Any:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    payload = _extract_json_payload(completed.stdout)
    return json.loads(payload)


def _extract_json_payload(stdout: str) -> str:
    clean = ANSI_ESCAPE_RE.sub("", stdout)
    lines = clean.splitlines()

    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("[") or stripped.startswith("{")):
            continue

        candidate_lines = lines[index:]
        while candidate_lines:
            candidate = "\n".join(candidate_lines).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                candidate_lines.pop()

    raise RuntimeError("Unable to parse JSON from kimaki output")


def _select_project_mapping(projects: list[dict[str, Any]], repo_root: Path) -> dict[str, Any] | None:
    repo_abs = _norm_path(str(repo_root))
    best: dict[str, Any] | None = None
    best_length = -1

    for item in projects:
        directory = item.get("directory")
        if not isinstance(directory, str):
            continue
        mapped = _norm_path(directory)
        if _is_path_prefix(mapped, repo_abs):
            if len(mapped) > best_length:
                best = item
                best_length = len(mapped)

    return best


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


def _is_path_prefix(parent: str, child: str) -> bool:
    parent_clean = parent.rstrip("\\/")
    child_clean = child.rstrip("\\/")
    if parent_clean == child_clean:
        return True
    return child_clean.startswith(parent_clean + os.sep)
