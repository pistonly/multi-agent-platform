# plan v2→v3 修订记录

## 变更来源

用户对话定案(2026-08-24 03:04,非 reviewer 项回应)。

## 变更内容

- **时序修正(核心)**:action_items 结构化时点从 close 时(解析 close_note 文本)提前到话题收敛时(Round Summary / ready),载体 = 话题文件夹 `action-items.yaml`(FS 事实源)。
- **投影义务替代 DB 建行**:server `fs_topic_progress_for_agent` 把 open 项投影为 kind=action_items obligation(与 stale_open_topics nudge 同构);waker 走既有 work_items 通道。v2 的「close 时 DB 建行 + alembic 051 弃 decision_id/topic_id FK」路线退役——零 schema 迁移,无 DB/FS 双源。
- **close 门禁升级为唯一防线**:action-items.yaml 存在 status: open 项 → 409;全 done/cancelled 放行。invariant:closed = 零尾款,只有 open 话题才有未完成项。
- **complete 必须带证据**(commit/pytest/文件路径,空证据拒绝);cancel 带理由不挡 close。
- **存量不回填**:已 close 话题不解析不追补(merge-github 案例证据已在 git 6aaca4c);close_note 文本 action_items 约定废弃,Skill(host-checklist/topic-host/wake.md)改引导收敛时落盘。

## 对 reviewer 的影响

0641c52c 核证的 FK 事实仍保留在调研表(备查,若未来恢复 DB 路线适用);v3 架构下不再触碰 topic_action_items 表。请在 result_review 阶段按 plan v3 验收(A1-A7)。

## 关联

- plan 文件:map/experiments/fs-close-action-items-lifecycle/plan-v3.md(= plan.md)
- git:map exp 3d519184 plan v3 commit
