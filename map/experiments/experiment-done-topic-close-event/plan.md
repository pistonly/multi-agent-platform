---
title: "实验 done→话题收尾事件桥：accept-result 分支触发 topic.close_pending wakeable 通知（不新增 work kind）"
acceptance:
  - "B1 accept-result 触发：`accept_result`（server/services/phase_service.py）在实验转 done 时向话题 creator 对应 agent 发一条 wakeable 通知；event 名 `topic.close_pending` **显式加入 WAKEABLE_NOTIFICATION_EVENTS 白名单**（该集合注释要求新事件显式声明+代码评审——本实验评审即为该评审）；summary 文案含话题 slug、实验短 id 与收尾指引，指引衔接 3d519184 close 门禁（确认话题 action-items.yaml 清零/全 done 后再 close）"
  - "B2 reject 不触发：`reject_result` 不发该通知（负向断言单测）；实验回 running 后再次 accept 正常重发（状态机只允许一次 accept→done 转换，天然幂等）"
  - "B3 creator/executor 分离语义：experiment.executor 与话题 creator persona 不同时，文案带 executor 名；相同或 executor 为空时文案不冗余带名"
  - "B4 不新增 work item kind：WORK_ITEM_KINDS registry、wake.md 分发表、work items 生成逻辑**零改动**——stale_open_topics 30 分钟 nudge 兜底互补不变（正道走事件通知，兜底留旧路径）"
  - "B5 FS 话题解析链：experiment.topic_id → FS 话题（uuid5 反解 slug，复用 fs_source_service 既有 workspace/视图设施）→ creator persona → agent id（复用 notification_service `_resolve_persona_agent_ids`）；存量 DB 话题（uuid 非 uuid5、无 FS 文件夹）降级：recipient 落 experiment creator agent，不 crash、不阻断 accept 主流程；**topic_id 为空**（无话题实验，实验列表存在 TOPIC='-' 先例）：**跳过通知**（无话题即无 close 收尾语义，不降级不发），accept 主流程不受影响"
  - "B6 事务一致：通知创建复用 notification_service 既有 after-commit 发布机制（_queue_created_after_commit），与 accept_result 同事务——accept 回滚则通知不发出"
  - "测试面：accept 触发（FS 话题 recipient/文案正确）、reject 不触发、executor 分离文案、白名单包含断言、DB 话题降级路径、topic_id 为空跳过、事务回滚不发 七组新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "pytest_summary:六组新增单测全绿(pytest_summary)"
  - "实测输出:测试项目内对齐实验 accept-result 后,`map work` notifications 出现 topic.close_pending 条目(category=wakeable,summary 含话题 slug 与收尾指引)(B1)"
  - "grep 核证:WAKEABLE_NOTIFICATION_EVENTS 含 topic.close_pending;WORK_ITEM_KINDS 与 wake.md 分发表零改动(B4)"
dependencies:
  - "话题 experiment-done-topic-close-event（2ca71c7e-27b7-5dd7-8c20-c62dab298a9b）close_note 口径：事件桥为主——notification 复用 wakeable 通道（topic_close_pending 语义，不新增 kind）于 accept-result 分支触发、reject 不触发、文案衔接 3d519184 新 close 门禁、stale nudge 兜底互补不变、creator/executor 分离时通知带 executor 名"
  - "实测背景：实验 4b1192cc 17:51 done → 话题 17:57 才 close，中间依赖 stale_open_topics nudge（阈值 30 分钟）+ waker 轮询周期；accept_result 现状零通知（仅 audit + action_item cascade）——`server/services/phase_service.py:565`"
  - "wakeable 通道：server/services/notification_service.py WAKEABLE_NOTIFICATION_EVENTS 显式白名单（新事件必须显式声明——本实验评审即声明评审）；recipient 解析复用 PERSONA_AGENT_NAMES/_resolve_persona_agent_ids"
  - "FS 话题 work items 生成里 active_exp_topic_ids 已排除活跃实验话题（fs_source_service.py:1183）——done 后话题脱离该集合，stale 路径自然接管，本实验不动它（B4）"
  - "与本批 fs-write-entry-validation（sdk/map_fs+cli）、ops-visibility-batch（cli audit）无代码冲突（本实验改 server/services/phase_service.py + notification_service.py）"
---

# 实验 done→话题收尾事件桥：accept-result 触发 wakeable 通知

## 背景

话题 `experiment-done-topic-close-event`：实验生命周期与话题生命周期之间没有事件桥——reviewer accept-result 后话题 creator（host）毫无感知，只能等 stale_open_topics 30 分钟 nudge 兜底才被唤醒收尾。实测时间线：实验 4b1192cc 17:51 phase=done → 话题 fs-advance-ack-validation 17:57 才 close，延迟与 stale 阈值强耦合。`accept_result` 现状零通知。

## 定稿决议（close_note 口径）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 事件桥为主：accept-result 分支触发 wakeable 通知，实验 done 瞬间 creator 收到「请收尾」义务唤醒 | close_note decision |
| D2 | 复用 wakeable 通道，不新增 work kind（topic_close_pending 语义走通知，不进 WORK_ITEM_KINDS/wake.md 表） | close_note decision |
| D3 | reject 不触发（驳回回 running 由 host 继续，无收尾义务） | close_note decision |
| D4 | 文案衔接 3d519184 close 门禁（action-items.yaml 清零后才可 close） | close_note decision |
| D5 | stale_open_topics nudge 兜底互补不变 | close_note decision |
| D6 | creator 与 executor 分离时通知带 executor 名 | close_note rationale 附补充 |

## 实施顺序

1. **I1 白名单与通知函数**（B1+B6）：`topic.close_pending` 入 WAKEABLE_NOTIFICATION_EVENTS；phase_service 内新增 `_notify_topic_close_pending(db, experiment)`（FS slug 反解 + creator persona→agent + executor 文案），接进 accept_result 同事务
2. **I2 单测**（B2+B3+B5）：accept 触发/reject 不触发/executor 分离/DB 话题降级/白名单断言/回滚不发
3. **I3 实测 evidence**：真实或测试项目跑一次 accept-result，`map work` 见 wakeable 条目落档

## 风险与边界

- FS slug 反解依赖 workspace 配置：若 server 侧拿不到 workspace（纯 DB 部署形态），走 B5 降级（recipient=experiment creator）——降级不 crash 不阻断
- 通知文案不越界替 host 做判断：只指引「收尾 close（先确认 action-items 清零）或继续推进」，不自动 close
- 事件名 `topic.close_pending` 是 notification event，不是 work item kind——与 D2「不新增 kind」的边界在实现与 review.yaml 里显式说明，防评审误解
