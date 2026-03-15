from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import run_once
from .task_loader import load_tasks


def main() -> None:
    parser = argparse.ArgumentParser(prog="smartworkmate")
    parser.add_argument("--repo-root", default=".", help="Repository root path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Parse and list task files")

    run_once_parser = subparsers.add_parser("run-once", help="Dispatch one task")
    run_once_parser.add_argument("--execute", action="store_true", help="Execute kimaki send for real")
    run_once_parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")

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

    execute = bool(args.execute and not args.dry_run)
    result = run_once(repo_root, execute=execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
