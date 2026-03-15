from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import run_once, update_task_state
from .setup import setup_auto
from .task_loader import load_tasks


def main() -> None:
    parser = argparse.ArgumentParser(prog="smartworkmate")
    parser.add_argument("--repo-root", default=".", help="Repository root path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Parse and list task files")

    setup_parser = subparsers.add_parser("setup", help="Auto-configure SmartWorkmate")
    setup_parser.add_argument("--auto", action="store_true", help="Detect config from running kimaki")
    setup_parser.add_argument("--force", action="store_true", help="Overwrite existing config.yaml")

    run_once_parser = subparsers.add_parser("run-once", help="Dispatch one task")
    run_once_parser.add_argument("--execute", action="store_true", help="Execute kimaki send for real")
    run_once_parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")

    update_parser = subparsers.add_parser("update-task", help="Update task state in local store")
    update_parser.add_argument("--task-id", required=True, help="Task ID")
    update_parser.add_argument("--status", required=True, help="New status")
    update_parser.add_argument("--pr-url", default="", help="PR URL to store")
    update_parser.add_argument("--notes", default="", help="Additional notes")

    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()

    if args.command == "scan":
        tasks = load_tasks(repo_root / "docs" / "tasks")
        payload = [
            {
                "task_id": task.task_id,
                "title": task.title,
                "priority": task.priority,
                "status": task.status.value,
                "path": str(task.path.relative_to(repo_root)),
            }
            for task in tasks
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "setup":
        if not args.auto:
            raise ValueError("Only auto setup is supported in MVP. Use: setup --auto")
        report = setup_auto(repo_root, force=bool(args.force))
        payload = {
            "result": "configured" if report.created else "already_exists",
            "config": str(report.config_path),
            "detected": report.detected,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "update-task":
        payload = update_task_state(
            repo_root,
            task_id=str(args.task_id),
            status=str(args.status),
            pr_url=str(args.pr_url),
            notes=str(args.notes),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    execute = bool(args.execute and not args.dry_run)
    result = run_once(repo_root, execute=execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
