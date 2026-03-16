from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import TaskStatus
from .task_loader import TaskFormatError, load_tasks


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-/\.]{2,}")
FINDING_SCAN_EXCLUDES = (
    ":(exclude)docs/tasks/auto/**",
)
AUTO_HRISK_LIMIT = 5
LOW_RISK_NOISE_PATH_PARTS = (
    "docs/tasks",
    "__pycache__",
    "node_modules",
    ".smartworkmate",
    "dist/",
    "build/",
)


def refresh_project_memory(repo_root: Path, *, max_commits: int = 80) -> dict[str, Any]:
    memory_dir = repo_root / ".smartworkmate" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    commits = _git_recent_commits(repo_root, max_commits=max_commits)
    tasks = _collect_task_files(repo_root)
    state_summary = _collect_state_summary(repo_root)
    hot_files = _git_hot_files(repo_root, max_commits=max_commits, top_n=20)
    task_outcomes = _collect_task_outcome_chunks(repo_root, max_items=40)
    chunks = _build_memory_chunks(
        repo_root,
        commits=commits,
        tasks=tasks,
        hot_files=hot_files,
        task_outcomes=task_outcomes,
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "commit_count": len(commits),
        "task_count": len(tasks),
        "commits": commits,
        "tasks": tasks,
        "hot_files": hot_files,
        "task_outcomes": task_outcomes,
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
        "hot_file_count": len(hot_files),
        "task_outcome_count": len(task_outcomes),
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

    commits = _git_recent_commits(repo_root, max_commits=max_commits)
    findings = _git_code_findings(repo_root)
    hot_files = _git_hot_files(repo_root, max_commits=max_commits, top_n=8)
    risk = _classify_risk(commits=commits, findings=findings)

    auto_dir = repo_root / "docs" / "tasks" / "auto"
    risk_dir = auto_dir / ("HRisk" if risk == "high" else "LRisk")
    risk_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"AUTO-{head_sha[:8]}-maintenance.md"
    target = risk_dir / file_name
    if target.exists():
        return {"result": "already_exists", "path": str(target), "risk": risk}

    unfinished = _unfinished_auto_tasks(repo_root)
    if risk == "high" and _count_unfinished_hrisk(unfinished) >= AUTO_HRISK_LIMIT:
        return {
            "result": "hrisk_limit_reached",
            "risk": risk,
            "limit": AUTO_HRISK_LIMIT,
            "path": str(risk_dir),
        }

    topic = _derive_topic(commits=commits, findings=findings, hot_files=hot_files)
    if _is_duplicate_unfinished(topic=topic, risk=risk, unfinished=unfinished):
        return {
            "result": "duplicate_unfinished",
            "risk": risk,
            "topic": topic,
        }

    summary_lines = [f"- {item['sha'][:7]} {item['subject']}" for item in commits[:8]]
    finding_lines = [f"- {item}" for item in findings[:10]]
    if not finding_lines:
        finding_lines = ["- No TODO/FIXME/HACK markers found in current scan"]

    base_branch = _detect_repo_base_branch(repo_root)
    smoke_command = _build_auto_smoke_command(repo_root=repo_root, hot_files=hot_files)
    focus_file = _pick_focus_file(repo_root=repo_root, hot_files=hot_files)
    low_risk_outline = _build_low_risk_outline(
        focus_file=focus_file,
        smoke_command=smoke_command,
        findings=findings,
    )

    content = {
        "task_id": f"AUTO-{head_sha[:8]}",
        "title": f"Auto {'high-risk' if risk == 'high' else 'low-risk'} maintenance: {topic}",
        "base_branch": base_branch,
        "priority": "high" if risk == "high" else "low",
        "status": "todo",
        "labels": ["auto", "maintenance", "hrisk" if risk == "high" else "lrisk"],
        "references": ["./MEMORY.md"],
    }

    markdown = (
        "---\n"
        + yaml.safe_dump(content, allow_unicode=False, sort_keys=False)
        + "---\n\n"
        + "## 任务需求\n\n"
        + (
            "基于最近提交记录和代码标记，提出高风险/大改动候选任务。该任务需要人工审阅并在最后添加 --FIN-- 后才会执行。\n\n"
            if risk == "high"
            else low_risk_outline["requirements"]
        )
        + (
            "近期提交摘要:\n"
            + ("\n".join(summary_lines) if summary_lines else "- (no recent commits)")
            + "\n\n"
            + "代码标记线索:\n"
            + "\n".join(finding_lines)
            + "\n\n"
            + "高频改动文件:\n"
            + ("\n".join(f"- {item['path']} ({item['touches']} touches)" for item in hot_files) if hot_files else "- (none)")
            + "\n\n"
            if risk == "high"
            else ""
        )
        + "## 任务设计\n\n"
        + (
            "先做影响面分析和回滚方案，再拆分为可审核的子改动。建议只在人工确认后推进。"
            if risk == "high"
            else low_risk_outline["design"]
        )
        + "\n\n"
        + "## 交付验收\n\n"
        + (
            "- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 可正常执行\n"
            + f"- [ ] `{smoke_command}` 可正常执行\n"
            + "- [ ] PR 描述清楚列出改进点、风险和回滚策略\n"
            if risk == "high"
            else low_risk_outline["acceptance"]
        )
        + ("\n--FIN--\n" if risk == "low" else "")
    )

    target.write_text(markdown, encoding="utf-8")
    return {
        "result": "created",
        "task_id": content["task_id"],
        "path": str(target),
        "risk": risk,
        "topic": topic,
        "base_branch": base_branch,
    }


def _detect_repo_base_branch(repo_root: Path) -> str:
    origin_head = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if origin_head.returncode == 0:
        value = origin_head.stdout.strip()
        if value.startswith("origin/") and len(value) > 7:
            return value[7:]

    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    branch = current.stdout.strip()
    if branch:
        return branch
    return "main"


def _build_auto_smoke_command(*, repo_root: Path, hot_files: list[dict[str, Any]]) -> str:
    for item in hot_files:
        path = str(item.get("path", "")).strip()
        if not path.endswith(".py"):
            continue
        target = repo_root / path
        if target.exists() and target.is_file():
            return f'uv run python -m py_compile "{path}"'

    for path in sorted(repo_root.rglob("*.py")):
        text = str(path)
        if any(skip in text for skip in (".venv", "node_modules", ".smartworkmate", "__pycache__")):
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except Exception:
            continue
        return f'uv run python -m py_compile "{rel}"'

    return "git status --short"


def _pick_focus_file(*, repo_root: Path, hot_files: list[dict[str, Any]]) -> str:
    for item in hot_files:
        raw = str(item.get("path", "")).strip().replace("\\", "/")
        if not raw or _is_noise_path(raw):
            continue
        target = repo_root / raw
        if not target.exists() or not target.is_file():
            continue
        if raw.endswith((".py", ".qml", ".md", ".json", ".toml")):
            return raw
    return "main.py"


def _is_noise_path(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in LOW_RISK_NOISE_PATH_PARTS)


def _build_low_risk_outline(*, focus_file: str, smoke_command: str, findings: list[str]) -> dict[str, str]:
    focus = focus_file.replace("\\", "/")
    candidate_issue = _extract_focus_finding(focus, findings)

    if focus.endswith(".py"):
        change_items = [
            f"在 `{focus}` 做一次小范围可审阅改进（不改变对外行为）。",
            "优先处理可确定的质量问题：冗余分支/无效语句/异常信息不清晰/重复逻辑片段。",
        ]
    elif focus.endswith(".qml"):
        change_items = [
            f"在 `{focus}` 做一次小范围 UI 结构或可维护性改进。",
            "保持视觉表现和交互语义不回退，减少重复或不一致写法。",
        ]
    else:
        change_items = [
            f"围绕 `{focus}` 做一次低风险、可回滚的小改动。",
            "改动必须是可验证的工程质量提升，而不是仅补充描述文字。",
        ]

    if candidate_issue:
        change_items.append(f"结合已发现线索处理该问题：`{candidate_issue}`。")

    requirements = "\n".join(["- " + item for item in change_items]) + "\n"

    design = (
        f"1. 先定位 `{focus}` 中最小可落地改动点，并限定影响范围。\n"
        "2. 在同一文件内完成修改，必要时补一处最小回归检查。\n"
        "3. 提交前确认改动可解释（why）且可回滚。\n"
        "4. 产生至少 1 次有效代码提交。"
    )

    acceptance = (
        f"- [ ] `git diff -- \"{focus}\"` 显示存在有效代码改动\n"
        f"- [ ] `{smoke_command}` 可正常执行\n"
        "- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 可正常执行\n"
        "- [ ] PR 描述包含改动原因（why）、风险和回滚方式\n"
    )

    return {
        "requirements": requirements,
        "design": design,
        "acceptance": acceptance,
    }


def _extract_focus_finding(focus_file: str, findings: list[str]) -> str:
    needle = focus_file.replace("\\", "/").lower()
    for item in findings:
        line = str(item).strip()
        if not line:
            continue
        lowered = line.replace("\\", "/").lower()
        if needle in lowered:
            return line
    return ""


def _classify_risk(*, commits: list[dict[str, str]], findings: list[str]) -> str:
    high_risk_tokens = (
        "refactor",
        "rewrite",
        "migrate",
        "architecture",
        "security",
        "auth",
        "new feature",
        "breaking",
        "major",
        "critical",
        "crash",
    )
    sample_text = "\n".join([item.get("subject", "") for item in commits[:12]] + findings[:12]).lower()
    if any(token in sample_text for token in high_risk_tokens):
        return "high"
    return "low"


def _derive_topic(
    *,
    commits: list[dict[str, str]],
    findings: list[str],
    hot_files: list[dict[str, Any]],
) -> str:
    if findings:
        topic = findings[0].split(":", 2)
        if len(topic) >= 2:
            return topic[0][-60:]
        return findings[0][:60]
    if hot_files:
        return str(hot_files[0].get("path", "maintenance"))[-60:]
    if commits:
        return commits[0].get("subject", "maintenance")[:60]
    return "maintenance"


def _unfinished_auto_tasks(repo_root: Path) -> list[dict[str, str]]:
    auto_dir = repo_root / "docs" / "tasks" / "auto"
    if not auto_dir.exists():
        return []

    try:
        tasks = load_tasks(auto_dir)
    except TaskFormatError:
        return []
    unfinished_statuses = {
        TaskStatus.TODO,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFY,
        TaskStatus.PR_OPEN,
        TaskStatus.REWORK,
        TaskStatus.BLOCKED,
    }
    out: list[dict[str, str]] = []
    for task in tasks:
        if task.status not in unfinished_statuses:
            continue
        risk = "high" if "hrisk" in {label.lower() for label in task.labels} else "low"
        out.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "risk": risk,
            }
        )
    return out


def _count_unfinished_hrisk(unfinished: list[dict[str, str]]) -> int:
    return sum(1 for item in unfinished if item.get("risk") == "high")


def _is_duplicate_unfinished(*, topic: str, risk: str, unfinished: list[dict[str, str]]) -> bool:
    normalized = _normalize_text(topic)
    if not normalized:
        return False
    for item in unfinished:
        if item.get("risk") != risk:
            continue
        title_norm = _normalize_text(item.get("title", ""))
        if normalized in title_norm or title_norm in normalized:
            return True
    return False


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
    task_outcomes: list[dict[str, str]],
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

    for item in task_outcomes:
        chunks.append(
            {
                "kind": "task_outcome",
                "id": item.get("task_id", ""),
                "text": item.get("text", ""),
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


def _collect_task_outcome_chunks(repo_root: Path, *, max_items: int) -> list[dict[str, str]]:
    state_path = repo_root / ".smartworkmate" / "state.json"
    if not state_path.exists():
        return []
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict):
        return []

    entries: list[tuple[str, dict[str, Any]]] = []
    for task_id, raw in tasks.items():
        if not isinstance(raw, dict):
            continue
        updated = str(raw.get("updated_at", ""))
        entries.append((updated, {"task_id": str(task_id), **raw}))
    entries.sort(key=lambda item: item[0], reverse=True)

    out: list[dict[str, str]] = []
    for _updated, item in entries[: max(1, max_items)]:
        task_id = str(item.get("task_id", ""))
        status = str(item.get("status", ""))
        notes = str(item.get("notes", "")).strip()
        failure = str(item.get("failure_detail", "")).strip()
        if not notes and not failure:
            continue
        text = f"{task_id} status={status}"
        if notes:
            text += f" notes={notes[:220]}"
        if failure:
            text += f" failure={failure[:220]}"
        out.append({"task_id": task_id, "text": text})
    return out


def _tokenize(text: str) -> set[str]:
    out = set()
    for token in TOKEN_RE.findall(text.lower()):
        out.add(token)
    return out


def _overlap_score(query_tokens: set[str], text_tokens: set[str]) -> int:
    if not query_tokens or not text_tokens:
        return 0
    return len(query_tokens.intersection(text_tokens))
