# Tasks Specification

Each task file must be a Markdown file under `docs/tasks/` with YAML frontmatter.

## Required frontmatter

```yaml
task_id: TSK-2026-001
title: Add token refresh flow
base_branch: main
priority: high
status: todo
labels: [feature, backend]
references:
  - ./designs/auth-sequence.puml
```

Only `task_id` and `title` are strictly required in MVP. Missing fields use defaults.

## Required sections

- `## 任务需求`
- `## 任务设计`
- `## 交付验收`

The `交付验收` section must include checkbox items like `- [ ] ...`.

Finalization gate (required):

- A task file is executable only when the file ends with `--FIN--` as the final marker.
- While drafting requirements/design, do not add `--FIN--`.
- After the task is frozen and ready for automation, append `--FIN--` on the last line.
- After `--FIN--` is added, do not edit the task content except by creating a new task file for follow-up work.

Status synchronization rule:

- If a task already has runtime state metadata (`run_id`/`branch`/`session`/`pr_url`), state is treated as source-of-truth and markdown `status` will be auto-updated.
- If a task has no runtime metadata yet, markdown `status` can still drive initial state.

Auto task folders:

- `docs/tasks/auto/LRisk/`: low-risk tasks, generated with `--FIN--`, eligible for auto execution.
- `docs/tasks/auto/HRisk/`: high-risk tasks, generated without `--FIN--`, require manual review and freeze.
- `HRisk` unfinished queue is capped at 5 to avoid unbounded growth.

When a checkbox line contains a command in backticks, `verify-task` will execute it automatically.
Examples:

- `- [ ] \\`uv run pytest tests/test_auth.py -q\\``
- `- [ ] \\`uv run python -m smartworkmate.cli --repo-root . scan\\``

Checks without runnable commands are treated as manual verification items.

## State model

- `todo`: not started
- `in_progress`: dispatched and under development
- `verify`: waiting for acceptance checks
- `pr_open`: pull request opened
- `done`: accepted and merged
- `rework`: failed checks / needs retry
- `blocked`: cannot proceed without external input

Local state (`.smartworkmate/state.json`) additionally tracks:

- `session_id`: detected Kimaki session ID for the task
- `thread_id`: Discord thread ID mapped to that session
- `pr_url`: pull request URL once opened

Done gate behavior:

- If `manual_approval_required` is `true` in `.smartworkmate/config.yaml`, a task in `verify` with PR will wait for explicit approval.
- Approve via:

```bash
uv run python -m smartworkmate.cli --repo-root . approve-task --task-id TSK-2026-001 --by iiishop
```

You can update `pr_url` automatically from a Kimaki session transcript:

```bash
uv run python -m smartworkmate.cli --repo-root . sync-task --task-id TSK-2026-001
```

Run global auto-discovery runner (recommended startup mode):

```bash
uv run python -m smartworkmate.cli start --root D:\workspace --execute --user iiishop
```

When no TODO/REWORK task is found in a project, the runner can draft one auto task under:

- `docs/tasks/auto/AUTO-<sha>-maintenance.md`
- draft contents include recent commits, TODO/FIXME markers, and hot-file hints
- marker scan excludes `docs/tasks/auto/**` to avoid recursive self-references from previous auto tasks

Run acceptance execution for a specific task:

```bash
uv run python -m smartworkmate.cli --repo-root . verify-task --task-id TSK-2026-001
```
