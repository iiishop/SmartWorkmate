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

1. Create a virtual environment and install dependencies.
2. Copy `.smartworkmate/config.example.yaml` to `.smartworkmate/config.yaml`.
3. Fill in Kimaki channel/user/session defaults.
4. Add task files under `docs/tasks/`.
5. Run a dry-run dispatch:

```bash
python -m smartworkmate.cli --repo-root . run-once --dry-run
```

Run a real dispatch:

```bash
python -m smartworkmate.cli --repo-root . run-once --execute
```

List parsed tasks:

```bash
python -m smartworkmate.cli --repo-root . scan
```

## Notes on cloud execution

This project can run fully in cloud environments, but real execution requires a worker environment with:

- `kimaki` CLI authenticated.
- git permissions to push branches.
- permission to create threads/sessions in your Discord setup.

GitHub Actions can be used with a self-hosted runner for full autonomy.
