---
task_id: TSK-2026-002
title: Reliability hardening for autonomous runner
base_branch: main
priority: high
status: todo
labels: [reliability, orchestration, safety]
references:
  - ./README.md
  - ./MEMORY.md
---

## 任务需求

提升 SmartWorkmate 的稳定性，重点覆盖幂等锁、失败重试、故障分类和崩溃恢复。
目标是把当前“能跑”的流程提升到“可持续无人值守运行”。

需要完成：

1. 同一项目同一 `task_id` 的调度应具备幂等保护，避免重复派发。
2. `kimaki send` / `git push` / `gh pr create` 增加统一重试策略（指数退避 + 最大重试）。
3. 将失败按类型写入状态（网络失败、权限失败、任务格式失败、命令执行失败）。
4. 重启后可恢复未完成任务（至少不丢状态，不重复派发）。

## 任务设计

建议新增一个运行时控制模块（例如 `smartworkmate/runtime_guard.py`），集中实现以下能力：

- 轻量锁文件（如 `.smartworkmate/locks/<task_id>.lock`）与 TTL 机制。
- 可复用重试执行器（包装 subprocess 调用）。
- 失败分类器（根据退出码和 stderr 关键词映射到错误类型）。
- 恢复逻辑：启动时扫描 state 中 `in_progress/pr_open/verify` 项并进入 reconcile。

请在 `start` 流程中接入这些能力，避免分散在多个函数里。

## 交付验收

- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 能识别 `TSK-2026-002`
- [ ] `uv run python -m smartworkmate.cli --repo-root . start --root "D:\workspace" --dry-run --once` 输出中包含可识别的可靠性控制信息（如 lock/retry/reconcile）
- [ ] `uv run python -m smartworkmate.cli --repo-root . verify-task --task-id TSK-2026-002` 返回结构化结果且不会崩溃
- [ ] 当同一 task 被重复触发时，状态中可观测到防重入行为（例如 skipped/locked 说明）
- [ ] 至少补充一段 README 文档，说明可靠性策略与恢复机制
