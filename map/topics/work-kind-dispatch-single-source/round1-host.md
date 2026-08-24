---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# wake.md 的 kind→清理分发表是手维护的第二真相源,server 加 kind 要人肉同步(host 发起)

## 原始问题

stale_open_topics 增加 FS 语义时(1b605e0b),**必须记得手动同步 .cursor/skills/map-project-collab/references/wake.md 的分发表**——忘了的话 agent 被唤醒后对着表找不到清理动作,义务变成死循环。action_items 断链(3d519184 修复中)同根:Skill 约定与 server 能力的 drift 没有机器防线。

分发表、TODO_BUCKET_UI_LABELS(cli/wake_backend.py)、server 产 kind 的三处,没有任何一致性校验。

## 期望(讨论口径)

1. 单源化方向 A:server/CLI 提供 `map work --explain <kind>`(或 `map work kinds`)输出清理动作,wake.md 表改为引用该命令
2. 方向 B:分发表由 server 生成(kinds registry → 渲染 md),CI 校验 wake.md 与 registry 一致
3. 新 kind 落地的 checklist 进 experiment-host/handler 文档

## 证据坐标

- 手维护表:.cursor/skills/map-project-collab/references/wake.md:12-28
- 人工同步先例:commit c381784(wake.md 行随 stale nudge 手动改)
- UI 标签第二处:cli/wake_backend.py TODO_BUCKET_UI_LABELS
