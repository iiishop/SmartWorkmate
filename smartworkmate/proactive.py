from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-/\.]{2,}")
FINDING_SCAN_EXCLUDES = (
    ":(exclude)docs/tasks/auto/**",
)


def refresh_project_memory(repo_root: Path, *, max_commits: int = 80) -> dict[str, Any]:
    memory_dir = repo_root / ".smartworkmate" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    commits = _git_recent_commits(repo_root, max_commits=max_commits)
    tasks = _collect_task_files(repo_root)
    state_summary = _collect_state_summary(repo_root)
    hot_files = _git_hot_files(repo_root, max_commits=max_commits, top_n=20)
    chunks = _build_memory_chunks(repo_root, commits=commits, tasks=tasks, hot_files=hot_files)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "commit_count": len(commits),
        "task_count": len(tasks),
        "commits": commits,
        "tasks": tasks,
        "hot_files": hot_files,
        "chunks": chunks,
        "state_summary": state_summary,
    }

    output = memory_dir / "project-memory.json"
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "result": "memory_refreshed",
        "output": str(output),
        "commit_count": len(commits),
        "task_count": len(tasks),
        "chunk_count": len(chunks),
    }


def query_project_memory(repo_root: Path, *, query: str, top_k: int = 5) -> dict[str, Any]:
    memory_path = repo_root / ".smartworkmate" / "memory" / "project-memory.json"
    if not memory_path.exists():
        refresh_project_memory(repo_root, max_commits=80)

    try:
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
    except Exception:
        refresh_project_memory(repo_root, max_commits=80)
        payload = json.loads(memory_path.read_text(encoding="utf-8"))

    query_tokens = _tokenize(query)
    if not query_tokens:
        return {"query": query, "results": []}

    chunks = payload.get("chunks", [])
    if not isinstance(chunks, list):
        return {"query": query, "results": []}

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in chunks:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        score = _overlap_score(query_tokens, _tokenize(text))
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    results = []
    for score, item in scored[: max(1, top_k)]:
        results.append(
            {
                "score": score,
                "kind": item.get("kind", "unknown"),
                "id": item.get("id", ""),
                "text": str(item.get("text", ""))[:500],
            }
        )

    return {
        "query": query,
        "results": results,
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
    hot_files = _git_hot_files(repo_root, max_commits=max_commits, top_n=8)

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
        + "高频改动文件:\n"
        + ("\n".join(f"- {item['path']} ({item['touches']} touches)" for item in hot_files) if hot_files else "- (none)")
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
        + "\n--FIN--\n"
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


def _git_hot_files(repo_root: Path, *, max_commits: int, top_n: int) -> list[dict[str, Any]]:
    command = ["git", "log", f"-n{max_commits}", "--name-only", "--pretty=format:"]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    counts: dict[str, int] = {}
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        counts[line] = counts.get(line, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{"path": path, "touches": touches} for path, touches in ranked[: max(1, top_n)]]


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
    command = [
        "git",
        "grep",
        "-n",
        "-E",
        "TODO|FIXME|HACK",
        "--",
        ".",
        *FINDING_SCAN_EXCLUDES,
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    findings: list[str] = []
    seen: set[str] = set()
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line or line in seen:
            continue
        findings.append(line)
        seen.add(line)
    return findings


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


def _build_memory_chunks(
    repo_root: Path,
    *,
    commits: list[dict[str, str]],
    tasks: list[str],
    hot_files: list[dict[str, Any]],
) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []

    for commit in commits[:120]:
        chunks.append(
            {
                "kind": "commit",
                "id": commit.get("sha", "")[:12],
                "text": commit.get("subject", ""),
            }
        )

    for task in tasks:
        chunks.append(
            {
                "kind": "task_file",
                "id": task,
                "text": task,
            }
        )

    for item in hot_files:
        chunks.append(
            {
                "kind": "hot_file",
                "id": str(item.get("path", "")),
                "text": f"{item.get('path', '')} touched {item.get('touches', 0)} times",
            }
        )

    readme = repo_root / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8", errors="replace")
        for index, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("#") or len(line) > 35:
                chunks.append(
                    {
                        "kind": "readme",
                        "id": f"README.md:{index}",
                        "text": line,
                    }
                )

    return chunks


def _tokenize(text: str) -> set[str]:
    out = set()
    for token in TOKEN_RE.findall(text.lower()):
        out.add(token)
    return out


def _overlap_score(query_tokens: set[str], text_tokens: set[str]) -> int:
    if not query_tokens or not text_tokens:
        return 0
    return len(query_tokens.intersection(text_tokens))
