from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_loader import TaskFormatError, load_task_file


def lint_tasks(repo_root: Path, *, relative_path: str = "") -> dict[str, Any]:
    tasks_root = repo_root / "docs" / "tasks"
    if not tasks_root.exists():
        return {
            "ok": False,
            "errors": ["docs/tasks directory not found"],
            "warnings": [],
            "files": [],
        }

    paths = _resolve_paths(tasks_root, relative_path)
    files: list[dict[str, Any]] = []
    all_errors: list[str] = []
    all_warnings: list[str] = []

    for path in paths:
        result: dict[str, Any] = {
            "path": str(path.relative_to(repo_root)),
            "errors": [],
            "warnings": [],
        }
        try:
            task = load_task_file(path)
        except TaskFormatError as error:
            message = str(error)
            result["errors"].append(message)
            all_errors.append(message)
            files.append(result)
            continue

        normalized = str(path).replace("\\", "/").lower()
        is_hrisk = "docs/tasks/auto/hrisk/" in normalized
        is_legacy_auto = "docs/tasks/auto/" in normalized and not is_hrisk and "/lrisk/" not in normalized
        if not task.finalized and not is_hrisk:
            message = f"{path}: missing terminal --FIN-- marker"
            if is_legacy_auto:
                result["warnings"].append(message + " (legacy auto task)")
                all_warnings.append(message + " (legacy auto task)")
            else:
                result["errors"].append(message)
                all_errors.append(message)

        if not task.references:
            message = f"{path}: no references provided (soft rule)"
            result["warnings"].append(message)
            all_warnings.append(message)

        if len(task.design.strip()) < 30:
            message = f"{path}: design section is very short (soft rule)"
            result["warnings"].append(message)
            all_warnings.append(message)

        command_checks = [check for check in task.acceptance_checks if "`" in check]
        if not command_checks:
            message = f"{path}: no runnable acceptance command found (soft rule)"
            result["warnings"].append(message)
            all_warnings.append(message)

        files.append(result)

    return {
        "ok": len(all_errors) == 0,
        "errors": all_errors,
        "warnings": all_warnings,
        "files": files,
    }


def _resolve_paths(tasks_root: Path, relative_path: str) -> list[Path]:
    if relative_path:
        candidate = (tasks_root / relative_path).resolve()
        if candidate.is_file():
            return [candidate]
        if candidate.is_dir():
            return _collect_markdown(candidate)
        return []
    return _collect_markdown(tasks_root)


def _collect_markdown(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        out.append(path)
    return out
