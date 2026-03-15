---
task_id: TSK-2026-001
title: Bootstrap SmartWorkmate orchestrator
base_branch: main
priority: high
status: done
labels: [foundation, automation]
references:
  - ./docs/tasks/README.md
---

## 任务需求

创建一个可以读取 `docs/tasks/*.md` 并分发一个任务到 Kimaki 的最小可运行系统。

## 任务设计

实现一个 Python CLI，包含 `scan` 和 `run-once` 两个命令。
`run-once` 在 dry-run 模式下只输出 `kimaki send` 命令，执行模式才真正发起任务。

## 交付验收

- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 可以列出任务
- [ ] `uv run python -m smartworkmate.cli --repo-root . run-once --dry-run` 返回任务分发信息
- [ ] `.smartworkmate/state.json` 被写入并包含任务状态

--FIN--
