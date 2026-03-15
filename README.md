# SmartWorkmate

SmartWorkmate is an autonomous engineering teammate framework built for `opencode` + `kimaki` workflows.

It watches `docs/tasks/*.md`, picks a task, prepares a worktree execution prompt, dispatches the task to Kimaki, and tracks lifecycle state (`todo -> in_progress -> verify -> pr_open -> done/rework`).

## Why this repository exists

- Convert task documents into executable engineering work.
- Keep a clear PR-based workflow with isolated git worktrees.
- Make hard acceptance criteria first-class and machine-checkable.
- Build long-term project memory to reduce hallucination.

## Current MVP scope

- Parse task Markdown files with YAML frontmatter.
- Validate required sections and acceptance criteria.
- Persist orchestration state in `.smartworkmate/state.json`.
- Dispatch one task run via `kimaki send` (dry-run supported).
- Generate execution context payload under `.smartworkmate/runs/`.
- Assign task-specific thread names and persist detected `session_id`/`thread_id` after execute.
- Support manual status updates (including PR URL) via CLI.
- Sync task PR URL/status from Kimaki session transcript.
- Run runnable acceptance checks and auto-update task state (`verify`/`rework`/`blocked`).

## Project layout

```text
smartworkmate/
  cli.py               # command entrypoint
  models.py            # task + status models
  task_loader.py       # markdown/frontmatter parser
  state_store.py       # local state persistence
  orchestrator.py      # scheduling + kimaki dispatch
docs/tasks/
  README.md            # task spec
  examples/
    TASK-0001-sample.md
```

## Quick start

1. Sync dependencies with `uv sync`.
2. Start the autonomous runner (single command):

```bash
uv run python -m smartworkmate.cli start --root D:\workspace --execute --user iiishop
```

This command automatically:

- detects Kimaki/OpenCode availability
- discovers projects from Kimaki mappings (or OpenCode session history)
- scans each project's `docs/tasks/*.md`
- starts a task session with `worktree + thread` (Kimaki mode)
- falls back to `git worktree + opencode run` when Kimaki is unavailable

Behavior note:

- Kimaki mode is asynchronous, so acceptance remains pending until session completion.
- OpenCode fallback mode runs synchronously and now auto-runs acceptance checks in the created worktree.

For safe testing use dry-run:

```bash
uv run python -m smartworkmate.cli start --root D:\workspace --dry-run --once
```

3. Optional: project-local setup command:

```bash
uv run python -m smartworkmate.cli --repo-root . setup --auto
```

4. Add task files under `docs/tasks/`.
5. Run a project-local dry-run dispatch:

```bash
uv run python -m smartworkmate.cli --repo-root . run-once --dry-run
```

Run a real dispatch:

```bash
uv run python -m smartworkmate.cli --repo-root . run-once --execute
```

List parsed tasks:

```bash
uv run python -m smartworkmate.cli --repo-root . scan
```

Update task state after PR events:

```bash
uv run python -m smartworkmate.cli --repo-root . update-task --task-id TSK-2026-001 --status pr_open --pr-url https://github.com/org/repo/pull/123
```

Sync task state from Kimaki session (auto-detect PR URL in transcript):

```bash
uv run python -m smartworkmate.cli --repo-root . sync-task --task-id TSK-2026-001
```

Run acceptance checks for one task:

```bash
uv run python -m smartworkmate.cli --repo-root . verify-task --task-id TSK-2026-001
```

## Notes on cloud execution

This project can run fully in cloud environments, but real execution requires a worker environment with:

- `kimaki` CLI authenticated.
- git permissions to push branches.
- permission to create threads/sessions in your Discord setup.

GitHub Actions can be used with a self-hosted runner for full autonomy.
