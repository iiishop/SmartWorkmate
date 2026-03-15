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

You can update `pr_url` automatically from a Kimaki session transcript:

```bash
uv run python -m smartworkmate.cli --repo-root . sync-task --task-id TSK-2026-001
```
