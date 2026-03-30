from __future__ import annotations

from pathlib import Path
import re

from smartworkmate.acceptance_spec.parser import parse_spec
from smartworkmate.acceptance_spec.semantic import validate_semantics

from .models import OpenCodeProject, OpenCodeSession, OpenCodeTaskRecord
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
_STATUS_RE = re.compile(r"^\s*status\s*:\s*.*$", re.MULTILINE)

_TASK_INDEX_BY_PROJECT: dict[str, dict[str, OpenCodeTaskRecord]] = {}
_TASK_ID_BY_PATH_BY_PROJECT: dict[str, dict[str, str]] = {}


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
    project_key = str(root.resolve())

    search_dirs = [tasks_root, tasks_root / "LRisk", tasks_root / "HRisk"]
    candidates: set[Path] = set()
    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            if path.is_file() and _has_exact_fin_last_line(path):
                candidates.add(path)

    sorted_candidates = sorted(candidates)
    _rebuild_task_index(project_key, sorted_candidates)
    return [str(path) for path in sorted_candidates]


def find_task_path_by_task_id(project_root: str, task_id: str) -> str:
    require_text("project_root", project_root)
    require_text("task_id", task_id)

    records = _get_task_index(project_root)
    wanted = task_id.strip()
    record = records.get(wanted)
    if record is None:
        raise ValueError(f"task_id not found: {wanted}")
    return record.path


def get_task_status_by_task_id(project_root: str, task_id: str) -> str:
    records = _get_task_index(project_root)
    wanted = task_id.strip()
    record = records.get(wanted)
    if record is None:
        raise ValueError(f"task_id not found: {wanted}")
    return record.status


def block_task(project_root: str, task_id: str) -> tuple[str, str]:
    require_text("project_root", project_root)
    require_text("task_id", task_id)

    path = Path(find_task_path_by_task_id(project_root, task_id))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[-1] == "--FIN--":
        lines = lines[:-1]
    path.write_text("\n".join(lines), encoding="utf-8")

    update_task_status(project_root, task_id, "blocked")

    project_key = str(Path(project_root.strip()).resolve())
    index = _TASK_INDEX_BY_PROJECT.get(project_key, {})
    index.pop(task_id.strip(), None)
    path_index = _TASK_ID_BY_PATH_BY_PROJECT.get(project_key, {})
    path_index.pop(str(path.resolve()), None)
    return task_id.strip(), "blocked"


def _has_exact_fin_last_line(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return False
    return lines[-1] == "--FIN--"


def read_task_requirements(project_root: str, task_id: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read_by_task_id(project_root, task_id)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    requirements = _extract_section(
        body,
        start_heading=_TASK_REQUIREMENTS_HEADING,
        end_heading=_TASK_DESIGN_HEADING,
        section_name="任务需求",
    )
    return task_id, requirements


def read_task_design(project_root: str, task_id: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read_by_task_id(project_root, task_id)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    design = _extract_section(
        body,
        start_heading=_TASK_DESIGN_HEADING,
        end_heading=_TASK_ACCEPTANCE_HEADING,
        section_name="任务设计",
    )
    return task_id, design


def read_task_acceptance(project_root: str, task_id: str) -> tuple[str, str]:
    path, task_id, body = _prepare_task_read_by_task_id(project_root, task_id)
    _ensure_duplicate_task_id_policy(project_root, task_id=task_id, target_path=path)

    acceptance = _extract_acceptance_section(body)
    asl_source = _extract_asl_source(acceptance)
    try:
        spec = parse_spec(asl_source)
        validate_semantics(spec)
    except ValueError as error:
        raise ValueError(f"ASL validation failed for {path}: {error}") from error
    return task_id, acceptance


def _prepare_task_read_by_task_id(project_root: str, task_id: str) -> tuple[Path, str, str]:
    require_text("project_root", project_root)
    require_text("task_id", task_id)

    path = Path(find_task_path_by_task_id(project_root, task_id.strip()))

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    stored_task_id = _extract_task_id_from_frontmatter(frontmatter)
    if not stored_task_id:
        raise ValueError(f"task_id is required in YAML frontmatter: {path}")
    return path, stored_task_id, body


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


def _extract_status_from_frontmatter(frontmatter: str) -> str:
    match = _STATUS_RE.search(frontmatter)
    if match is None:
        return ""
    line = match.group(0)
    _, _, value = line.partition(":")
    return value.strip().strip("\"'")


def update_task_status(
    project_root: str,
    task_id: str,
    new_status: str,
) -> tuple[str, str]:
    require_text("project_root", project_root)
    require_text("task_id", task_id)
    require_text("new_status", new_status)

    path = Path(find_task_path_by_task_id(project_root, task_id))

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    stored_task_id = _extract_task_id_from_frontmatter(frontmatter)
    if not stored_task_id:
        raise ValueError(f"task_id is required in YAML frontmatter: {path}")
    if stored_task_id != task_id.strip():
        raise ValueError(
            f"task_id mismatch for {path}: expected {task_id.strip()}, got {stored_task_id}"
        )

    status_line = f"status: {new_status.strip()}"
    if _STATUS_RE.search(frontmatter):
        new_frontmatter = _STATUS_RE.sub(status_line, frontmatter, count=1)
    else:
        suffix = "\n" if frontmatter.endswith("\n") else ""
        new_frontmatter = frontmatter + suffix + status_line

    rebuilt = f"---\n{new_frontmatter}\n---\n{body}"
    path.write_text(rebuilt, encoding="utf-8")
    project_key = str(Path(project_root.strip()).resolve())
    records = _TASK_INDEX_BY_PROJECT.setdefault(project_key, {})
    records[stored_task_id] = OpenCodeTaskRecord(
        task_id=stored_task_id,
        path=str(path),
        status=new_status.strip(),
        mtime=path.stat().st_mtime,
    )
    return stored_task_id, new_status.strip()


def _get_task_index(project_root: str) -> dict[str, OpenCodeTaskRecord]:
    root = Path(project_root.strip())
    project_key = str(root.resolve())
    if project_key not in _TASK_INDEX_BY_PROJECT:
        scan_task_markdown_documents(project_root)
    return _TASK_INDEX_BY_PROJECT.get(project_key, {})


def _rebuild_task_index(project_key: str, files: list[Path]) -> None:
    previous_by_path = _TASK_ID_BY_PATH_BY_PROJECT.get(project_key, {})
    next_by_task: dict[str, OpenCodeTaskRecord] = {}
    next_by_path: dict[str, str] = {}

    for path in files:
        text = path.read_text(encoding="utf-8")
        frontmatter, _ = _split_frontmatter(text)
        task_id = _extract_task_id_from_frontmatter(frontmatter)
        if not task_id:
            raise ValueError(f"task_id is required in YAML frontmatter: {path}")

        resolved_path = str(path.resolve())
        previous_task_id = previous_by_path.get(resolved_path)
        if previous_task_id is not None and previous_task_id != task_id:
            raise ValueError(
                f"task_id is immutable for path {path}: {previous_task_id} -> {task_id}"
            )

        status = _extract_status_from_frontmatter(frontmatter) or "todo"
        if task_id in next_by_task:
            existing = next_by_task[task_id]
            existing_path = Path(existing.path)
            existing_mtime = existing.mtime
            current_mtime = path.stat().st_mtime
            newer = path if current_mtime >= existing_mtime else existing_path
            older = existing_path if newer.resolve() == path.resolve() else path
            raise ValueError(
                f"duplicate task_id {task_id} detected: blocked newer task {newer}; conflicts with {older}"
            )

        next_by_task[task_id] = OpenCodeTaskRecord(
            task_id=task_id,
            path=str(path),
            status=status,
            mtime=path.stat().st_mtime,
        )
        next_by_path[resolved_path] = task_id

    _TASK_INDEX_BY_PROJECT[project_key] = next_by_task
    _TASK_ID_BY_PATH_BY_PROJECT[project_key] = next_by_path
