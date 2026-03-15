from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def refresh_project_memory(repo_root: Path, *, max_commits: int = 80) -> dict[str, Any]:
    memory_dir = repo_root / ".smartworkmate" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    commits = _git_recent_commits(repo_root, max_commits=max_commits)
    tasks = _collect_task_files(repo_root)
    state_summary = _collect_state_summary(repo_root)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "commit_count": len(commits),
        "task_count": len(tasks),
        "commits": commits,
        "tasks": tasks,
        "state_summary": state_summary,
    }

    output = memory_dir / "project-memory.json"
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "result": "memory_refreshed",
        "output": str(output),
        "commit_count": len(commits),
        "task_count": len(tasks),
    }


def create_idle_improvement_task(repo_root: Path, *, max_commits: int = 20) -> dict[str, Any]:
    head_sha = _git_head_sha(repo_root)
    if not head_sha:
        return {"result": "skipped", "reason": "no git head sha"}

    auto_dir = repo_root / "docs" / "tasks" / "auto"
    auto_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"AUTO-{head_sha[:8]}-maintenance.md"
    target = auto_dir / file_name
    if target.exists():
        return {"result": "already_exists", "path": str(target)}

    commits = _git_recent_commits(repo_root, max_commits=max_commits)
    findings = _git_code_findings(repo_root)

    summary_lines = [f"- {item['sha'][:7]} {item['subject']}" for item in commits[:8]]
    finding_lines = [f"- {item}" for item in findings[:10]]
    if not finding_lines:
        finding_lines = ["- No TODO/FIXME/HACK markers found in current scan"]

    content = {
        "task_id": f"AUTO-{head_sha[:8]}",
        "title": "Auto maintenance review from recent commits",
        "base_branch": "main",
        "priority": "low",
        "status": "todo",
        "labels": ["auto", "maintenance"],
        "references": ["./MEMORY.md"],
    }

    markdown = (
        "---\n"
        + yaml.safe_dump(content, allow_unicode=False, sort_keys=False)
        + "---\n\n"
        + "## 任务需求\n\n"
        + "基于最近提交记录和代码标记，挑选 1-2 个低风险改进点，形成一个清晰可审阅的 PR。\n\n"
        + "近期提交摘要:\n"
        + ("\n".join(summary_lines) if summary_lines else "- (no recent commits)")
        + "\n\n"
        + "代码标记线索:\n"
        + "\n".join(finding_lines)
        + "\n\n"
        + "## 任务设计\n\n"
        + "先聚焦可快速验证的小改动（重构、注释修正、轻量 bugfix、测试补全）。"
        + "实现时保持多次 commit，PR 描述中明确 why 和风险。\n\n"
        + "## 交付验收\n\n"
        + "- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 可正常执行\n"
        + "- [ ] `uv run python -m smartworkmate.cli --repo-root . verify-task --task-id AUTO-"
        + head_sha[:8]
        + "` 返回结构化结果\n"
        + "- [ ] PR 描述清楚列出改进点、风险和回滚策略\n"
    )

    target.write_text(markdown, encoding="utf-8")
    return {
        "result": "created",
        "task_id": content["task_id"],
        "path": str(target),
    }


def _git_recent_commits(repo_root: Path, *, max_commits: int) -> list[dict[str, str]]:
    command = ["git", "log", f"-n{max_commits}", "--pretty=format:%H|%cI|%s"]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    out = completed.stdout.strip()
    if not out:
        return []
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def _git_head_sha(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return completed.stdout.strip()


def _git_code_findings(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "grep", "-n", "-E", "TODO|FIXME|HACK"],
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return lines


def _collect_task_files(repo_root: Path) -> list[str]:
    tasks_dir = repo_root / "docs" / "tasks"
    if not tasks_dir.exists():
        return []
    return [str(path.relative_to(repo_root)) for path in sorted(tasks_dir.rglob("*.md"))]


def _collect_state_summary(repo_root: Path) -> dict[str, int]:
    state_path = repo_root / ".smartworkmate" / "state.json"
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    counts: dict[str, int] = {}
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict):
        return counts
    for value in tasks.values():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts
