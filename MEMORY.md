# MEMORY

- SmartWorkmate is driven by task files in `docs/tasks/*.md`.
- Required task sections are `任务需求`, `任务设计`, and `交付验收` with checkbox acceptance items.
- Runtime uses Kimaki as orchestration transport and OpenCode as execution engine.
- Current preferred execution model is `worktree + PR`, with one task expected to map to one conversation thread.
- Auto setup should detect local Kimaki/OpenCode environment and generate `.smartworkmate/config.yaml`.
- Use `uv run python -m smartworkmate.cli --repo-root . <command>` in this repository.
