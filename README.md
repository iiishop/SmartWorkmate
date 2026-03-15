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
- Done gate: move `verify -> done` only after PR exists and (optionally) manual approval.
- Refresh persistent project memory snapshot and generate idle improvement task drafts.
- Keep `.smartworkmate/state.json` and `docs/tasks/*.md` status values synchronized automatically.
- Execute tasks only when task markdown is finalized with terminal marker `--FIN--`.

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
- discovers projects from OpenCode project roots first, then enriches with Kimaki channel mappings
- refreshes `.smartworkmate/memory/project-memory.json` for each project
- synchronizes state and markdown status before task decisions
- scans each project's `docs/tasks/*.md`
- reconciles active tasks first (sync PR URL from Kimaki, then try acceptance)
- starts a task session with `worktree + thread` (Kimaki mode)
- defaults to isolated local execution (`git worktree + opencode run`) and keeps Kimaki for coordination
- when no task is available, can generate one draft under `docs/tasks/auto/`
- enriches task execution prompts with top relevant memory snippets from project history

Auto task risk policy:

- Auto tasks are generated under `docs/tasks/auto/LRisk/` or `docs/tasks/auto/HRisk/`.
- `LRisk` tasks are generated with `--FIN--` and can run automatically.
- `HRisk` tasks are generated without `--FIN--`; they never run until a human appends `--FIN--`.
- To limit noise, unfinished `HRisk` auto tasks are capped at 5.
- Auto task generation checks unfinished auto tasks to avoid duplicate topics.

Behavior note:

- Kimaki mode is asynchronous, so acceptance remains pending until session completion.
- OpenCode fallback mode runs synchronously and now auto-runs acceptance checks in the created worktree.
- Reconcile step can auto-create missing PRs and enforce done gating.

## Reliability hardening

SmartWorkmate now includes runtime reliability controls for unattended execution:

- **Idempotent task lock**: dispatch acquires `.smartworkmate/locks/<task_id>.lock` with TTL before sending work.
  A duplicated trigger for the same task is marked as `skipped_locked` and will not dispatch again.
- **Unified retries**: `kimaki send`, `git push`, and `gh pr create` use a shared retry strategy
  (exponential backoff, max retries) for transient network failures.
- **Failure typing in state**: task records persist `failure_type` and `failure_detail` with
  normalized types (`network_failure`, `permission_failure`, `task_format_failure`, `command_execution_failure`).
- **Crash recovery / reconcile**: startup cycle always reconciles `in_progress`, `pr_open`, and `verify`
  records first, so unfinished tasks are resumed from state and not redispatched blindly.
- **Branch/PR guard**: PR creation now requires branch existence and commits ahead of base.
- **Isolation policy**: execution backend is configurable; default backend is `opencode_local` with worktree isolation enabled.

## Task finalization rule

- A task markdown is eligible for execution only when its final line is `--FIN--`.
- This acts as a freeze marker. After adding it, treat the task content as immutable.

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

Approve a verified task so reconcile can move it to `done`:

```bash
uv run python -m smartworkmate.cli --repo-root . approve-task --task-id TSK-2026-001 --by iiishop
```

Sync task state from Kimaki session (auto-detect PR URL in transcript):

```bash
uv run python -m smartworkmate.cli --repo-root . sync-task --task-id TSK-2026-001
```

Run acceptance checks for one task:

```bash
uv run python -m smartworkmate.cli --repo-root . verify-task --task-id TSK-2026-001
```

Refresh project memory snapshot:

```bash
uv run python -m smartworkmate.cli --repo-root . memory-refresh
```

Query project memory snapshot:

```bash
uv run python -m smartworkmate.cli --repo-root . memory-query --query "auth retry policy"
```

Generate one idle improvement task:

```bash
uv run python -m smartworkmate.cli --repo-root . idle-task
```

## Notes on cloud execution

This project can run fully in cloud environments, but real execution requires a worker environment with:

- `kimaki` CLI authenticated.
- git permissions to push branches.
- permission to create threads/sessions in your Discord setup.

GitHub Actions can be used with a self-hosted runner for full autonomy.
