from __future__ import annotations

import argparse
import json
from pathlib import Path

from .acceptance import verify_task_acceptance
from .auto_runner import start_autonomous_runner
from .orchestrator import approve_task, run_once, sync_task_from_kimaki, update_task_state
from .proactive import create_idle_improvement_task, query_project_memory, refresh_project_memory
from .setup import setup_auto
from .status_sync import sync_state_and_tasks, sync_state_and_tasks_with_options
from .task_lint import lint_tasks
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

    approve_parser = subparsers.add_parser("approve-task", help="Approve a verified task for done transition")
    approve_parser.add_argument("--task-id", required=True, help="Task ID")
    approve_parser.add_argument("--by", default="iiishop", help="Approver name")

    sync_parser = subparsers.add_parser("sync-task", help="Sync task PR state from kimaki session")
    sync_parser.add_argument("--task-id", required=True, help="Task ID")

    verify_parser = subparsers.add_parser("verify-task", help="Run runnable acceptance checks for one task")
    verify_parser.add_argument("--task-id", required=True, help="Task ID")
    verify_parser.add_argument(
        "--fail-on-manual-only",
        action="store_true",
        help="Mark task blocked when no runnable commands are found",
    )

    start_parser = subparsers.add_parser("start", help="Start autonomous task runner")
    start_parser.add_argument("--root", default=".", help="Root directory used for project discovery")
    start_parser.add_argument("--execute", action="store_true", help="Execute real dispatches")
    start_parser.add_argument("--dry-run", action="store_true", help="Force dry-run dispatch mode")
    start_parser.add_argument("--once", action="store_true", help="Run a single polling cycle")
    start_parser.add_argument("--interval", type=int, default=300, help="Polling interval in seconds")
    start_parser.add_argument("--user", default="iiishop", help="Kimaki username for new threads")

    memory_parser = subparsers.add_parser("memory-refresh", help="Refresh project memory snapshot")
    memory_parser.add_argument("--max-commits", type=int, default=80, help="Number of commits to index")

    memory_query_parser = subparsers.add_parser("memory-query", help="Query project memory snapshot")
    memory_query_parser.add_argument("--query", required=True, help="Search query text")
    memory_query_parser.add_argument("--top-k", type=int, default=5, help="Max results")

    idle_parser = subparsers.add_parser("idle-task", help="Generate one auto improvement task draft")
    idle_parser.add_argument("--max-commits", type=int, default=20, help="Recent commits to inspect")

    lint_parser = subparsers.add_parser("lint-task", help="Lint task markdown files")
    lint_parser.add_argument("--path", default="", help="Relative path under docs/tasks to lint")
    lint_parser.add_argument("--strict", action="store_true", help="Exit with non-zero code on warnings")

    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()

    if args.command != "setup":
        sync_state_and_tasks(repo_root)

    if args.command == "scan":
        sync_result = sync_state_and_tasks(repo_root)
        tasks = load_tasks(repo_root / "docs" / "tasks")
        payload = [
            {
                "task_id": task.task_id,
                "title": task.title,
                "priority": task.priority,
                "status": task.status.value,
                "finalized": task.finalized,
                "path": str(task.path.relative_to(repo_root)),
            }
            for task in tasks
        ]
        payload = {
            "sync": sync_result,
            "tasks": payload,
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
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
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "update-task":
        payload = update_task_state(
            repo_root,
            task_id=str(args.task_id),
            status=str(args.status),
            pr_url=str(args.pr_url),
            notes=str(args.notes),
        )
        payload["sync"] = sync_state_and_tasks_with_options(
            repo_root,
            force_state_task_ids={str(args.task_id)},
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "approve-task":
        payload = approve_task(
            repo_root,
            task_id=str(args.task_id),
            approver=str(args.by),
        )
        payload["sync"] = sync_state_and_tasks_with_options(
            repo_root,
            force_state_task_ids={str(args.task_id)},
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "sync-task":
        payload = sync_task_from_kimaki(repo_root, task_id=str(args.task_id))
        payload["sync"] = sync_state_and_tasks_with_options(
            repo_root,
            force_state_task_ids={str(args.task_id)},
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "verify-task":
        payload = verify_task_acceptance(
            repo_root,
            task_id=str(args.task_id),
            fail_on_manual_only=bool(args.fail_on_manual_only),
        )
        payload["sync"] = sync_state_and_tasks_with_options(
            repo_root,
            force_state_task_ids={str(args.task_id)},
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "start":
        execute = bool(args.execute and not args.dry_run)
        payload = start_autonomous_runner(
            root=Path(args.root).resolve(),
            execute=execute,
            once=bool(args.once or not execute),
            interval_seconds=max(30, int(args.interval)),
            user=str(args.user),
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "memory-refresh":
        payload = refresh_project_memory(repo_root, max_commits=max(10, int(args.max_commits)))
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "memory-query":
        payload = query_project_memory(
            repo_root,
            query=str(args.query),
            top_k=max(1, int(args.top_k)),
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "idle-task":
        payload = create_idle_improvement_task(repo_root, max_commits=max(5, int(args.max_commits)))
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    if args.command == "lint-task":
        payload = lint_tasks(repo_root, relative_path=str(args.path))
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        if not payload.get("ok", False):
            raise SystemExit(2)
        if bool(args.strict) and payload.get("warnings"):
            raise SystemExit(3)
        return

    execute = bool(args.execute and not args.dry_run)
    result = run_once(repo_root, execute=execute)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
