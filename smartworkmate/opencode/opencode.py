from __future__ import annotations

from pathlib import Path
import re

from smartworkmate.acceptance_spec.parser import parse_spec
from smartworkmate.acceptance_spec.semantic import validate_semantics

from .models import OpenCodeProject, OpenCodeSession
from .tools import normalize_directory, require_text, run_json_command


_PROJECTS_SQL = (
    "SELECT id, name, worktree, time_updated "
    "FROM project "
    "WHERE worktree IS NOT NULL AND worktree != '/' "
    "ORDER BY time_updated DESC;"
)

_TASK_REQUIREMENTS_HEADING = re.compile(r"^##\s*任务需求\s*$", re.MULTILINE)
_TASK_DESIGN_HEADING = re.compile(r"^##\s*任务设计\s*$", re.MULTILINE)
_TASK_ACCEPTANCE_HEADING = re.compile(r"^##\s*交付验收\s*$", re.MULTILINE)
_ASL_FENCE_RE = re.compile(r"```asl\s*\n([\s\S]*?)\n```", re.IGNORECASE)
_TASK_ID_RE = re.compile(r"^\s*task_id\s*:\s*(.*?)\s*$", re.MULTILINE)


def list_projects() -> list[OpenCodeProject]:
    rows = run_json_command(["db", _PROJECTS_SQL, "--format", "json"])
    if not isinstance(rows, list):
        return []

    projects: list[OpenCodeProject] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        worktree = str(row.get("worktree", "")).strip()
        if not worktree or worktree == "/":
            continue
        projects.append(
            OpenCodeProject(
                id=str(row.get("id", "")),
                name=str(row.get("name", "")),
                worktree=worktree,
                time_updated=str(row.get("time_updated", "")),
                raw=row,
            )
        )
    return projects


def list_project_sessions(project_worktree: str) -> list[OpenCodeSession]:
    require_text("project_worktree", project_worktree)
    target = normalize_directory(project_worktree)

    rows = run_json_command(
        ["session", "list", "--format", "json", "--max-count", "300"]
    )
    if not isinstance(rows, list):
        return []

    sessions: list[OpenCodeSession] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        directory = str(row.get("directory", "")).strip()
        if not directory:
            continue
        if normalize_directory(directory) != target:
            continue
        sessions.append(
            OpenCodeSession(
                id=str(row.get("id", "")),
                directory=directory,
                title=str(row.get("title", "")),
                raw=row,
            )
        )
    return sessions


def scan_task_markdown_documents(project_root: str) -> list[str]:
    require_text("project_root", project_root)
    root = Path(project_root.strip())
    tasks_root = root / "docs" / "tasks"

    search_dirs = [tasks_root, tasks_root / "LRisk", tasks_root / "HRisk"]
    candidates: set[Path] = set()
    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            if path.is_file() and _has_exact_fin_last_line(path):
                candidates.add(path)

    return [str(path) for path in sorted(candidates)]


def _has_exact_fin_last_line(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return False
    return lines[-1] == "--FIN--"


def read_task_requirements(project_root: str, task_markdown_path: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read(project_root, task_markdown_path)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    requirements = _extract_section(
        body,
        start_heading=_TASK_REQUIREMENTS_HEADING,
        end_heading=_TASK_DESIGN_HEADING,
        section_name="任务需求",
    )
    return task_id, requirements


def read_task_design(project_root: str, task_markdown_path: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read(project_root, task_markdown_path)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    design = _extract_section(
        body,
        start_heading=_TASK_DESIGN_HEADING,
        end_heading=_TASK_ACCEPTANCE_HEADING,
        section_name="任务设计",
    )
    return task_id, design


def read_task_acceptance(project_root: str, task_markdown_path: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read(project_root, task_markdown_path)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    acceptance = _extract_acceptance_section(body)
    asl_source = _extract_asl_source(acceptance)
    try:
        spec = parse_spec(asl_source)
        validate_semantics(spec)
    except ValueError as error:
        raise ValueError(f"ASL validation failed for {path}: {error}") from error
    return task_id, acceptance


def _prepare_task_read(project_root: str, task_markdown_path: str) -> tuple[Path, str, str]:
    require_text("project_root", project_root)
    require_text("task_markdown_path", task_markdown_path)

    root = Path(project_root.strip())
    path = Path(task_markdown_path.strip())
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise ValueError(f"task markdown not found: {path}")

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    task_id = _extract_task_id_from_frontmatter(frontmatter)
    if not task_id:
        raise ValueError(f"task_id is required in YAML frontmatter: {path}")
    return path, task_id, body


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("task markdown must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("task markdown frontmatter must end with closing ---")
    return text[4:end], text[end + 5 :]


def _extract_section(
    body: str,
    *,
    start_heading: re.Pattern[str],
    end_heading: re.Pattern[str],
    section_name: str,
) -> str:
    start = start_heading.search(body)
    if start is None:
        raise ValueError(f"cannot find section heading: {section_name}")
    end = end_heading.search(body, start.end())
    if end is None:
        raise ValueError(f"cannot find ending heading after section: {section_name}")
    return body[start.end() : end.start()].lstrip("\n").rstrip("\n")


def _extract_acceptance_section(body: str) -> str:
    start = _TASK_ACCEPTANCE_HEADING.search(body)
    if start is None:
        raise ValueError("cannot find section heading: 交付验收")

    section = body[start.end() :].lstrip("\n")
    lines = section.splitlines()
    if not lines:
        raise ValueError("交付验收 section is empty")
    if lines[-1] != "--FIN--":
        raise ValueError("task markdown must end with exact --FIN-- line")
    return "\n".join(lines[:-1]).rstrip("\n")


def _extract_asl_source(acceptance: str) -> str:
    match = _ASL_FENCE_RE.search(acceptance)
    if match is not None:
        return match.group(1).strip()
    return acceptance.strip()


def _ensure_duplicate_task_id_policy(
    project_root: str,
    *,
    task_id: str,
    target_path: Path,
) -> None:
    root = Path(project_root.strip())
    tasks_root = root / "docs" / "tasks"
    if not tasks_root.exists():
        return

    by_task_id: dict[str, list[Path]] = {}
    for path in tasks_root.rglob("*.md"):
        if not path.is_file():
            continue
        if path.name.lower() in {"readme.md", "template.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        frontmatter, _ = _split_frontmatter(text)
        candidate_task_id = _extract_task_id_from_frontmatter(frontmatter)
        if not candidate_task_id:
            raise ValueError(f"task_id is required in YAML frontmatter: {path}")
        by_task_id.setdefault(candidate_task_id, []).append(path)

    duplicates = by_task_id.get(task_id, [])
    if len(duplicates) <= 1:
        return

    newest = max(duplicates, key=lambda item: item.stat().st_mtime)
    if target_path.resolve() != newest.resolve():
        return

    others = [item for item in duplicates if item.resolve() != newest.resolve()]
    refs = ", ".join(str(item) for item in others)
    raise ValueError(
        f"task_id {task_id} duplicate detected: blocked newer task {newest}; conflicts with {refs}"
    )


def _extract_task_id_from_frontmatter(frontmatter: str) -> str:
    match = _TASK_ID_RE.search(frontmatter)
    if match is None:
        return ""
    value = match.group(1).strip()
    if not value:
        return ""
    if (value.startswith("\"") and value.endswith("\"")) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1].strip()
    return value
