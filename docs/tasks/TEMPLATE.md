---
task_id: TSK-2026-001
title: Short task title
base_branch: main
priority: high
status: done
labels: [feature]
references:
  - ./README.md
---

## 任务需求

写清楚目标、范围、非目标。说明最终要看到什么结果。

## 任务设计

写清关键方案、边界和风险。若有图纸或设计文档，务必放进 references。

## 交付验收

- [ ] `uv run python -m smartworkmate.cli --repo-root . scan` 可以正常执行
- [ ] `python -m unittest discover -s tests -p "test_*.py"` 关键测试通过
- [ ] PR 描述包含变更点、风险和回滚策略

--FIN--
