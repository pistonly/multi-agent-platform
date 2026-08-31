---
title: "FS 话题事件通知按角色扇出收窄 + digest 自清（reviewer waker 空唤醒根治）"
acceptance:
  - "A1 角色白名单每轮重算：notification fan-out 白名单 = topic 元数据派生的 `creator ∪ declared ∪ speakers_current_round ∪ speakers_prev_round`，每轮重算（derived view，不静态缓存）——保证 round1 接力过来的 speaker 进入 round2 白名单，unread_change 接力不断"
  - "A2 contextual vs obligation 双层过滤：filter 路径对 **contextual kind（含 `unread_change`）按角色白名单过滤**；**obligation kind 全量 fan-out 给 reviewer**（reviewer 永远在 obligation kind 的白名单里，不受 participants 列表约束）——这是 reviewer round2 第 17-22 行的硬边界，不可妥协"
  - "A3 KINDS registry 增加 `required_role`：server/services/work_kinds.py 每条 kind 增 `required_role`（host/participant/reviewer/all），work item 生成时按角色 + participants 派生白名单；filter 层按 `(kind.required_role, agent.role) ∈ whitelist` 判定 fan-out 目标"
  - "A4 obligation 豁免强制：`required_role=reviewer|all` 且 kind.priority=obligation 时，filter 必须豁免白名单检查——reviewer 在未参与任何 topic 的情况下也能收到 experiment.phase_changed(review) / experiment.phase_changed(result_review) / experiment.lifecycle.cancelled / experiment.lifecycle.withdrawn；regression 测试覆盖该豁免（reviewer round2 第 18-22 行）"
  - "A5 digest 自清（仅 digest 类）：notification cleanup 服务周期任务（建议 24h）扫描 unread notification，`category=digest` 且 `first_event_at` 距今 ≥7 天自动 read——**严禁清 obligation 类**（reviewer round2 第 30-31 行：豁免 `experiment.phase_changed`/`experiment.lifecycle.*` 的 obligation-wakeable 即便 7 天未读）"
  - "A6 审计日志：所有被自清的 digest 通知必须写 `notification_audit_log` 表/文件（who=system / when=执行时间 / why=`auto_drain_digest_7d` / target_id=被清 notification id），便于复盘"
  - "A7 回归测试强制：`tests/test_notification_fanout.py` 新增 — 构造「话题事件通知仅波及非参与者 reviewer」场景断言 reviewer wakeable 面为空；`tests/test_work_kinds.py` 新增 — `required_role` 字段对 obligation kind 豁免的 case；`tests/test_notification_cleanup.py` 新增 — digest 与 obligation 混合积压下 obligation 不被自动 read、digest 自清写审计"
  - "A8 验收：`ruff check` 通过；`pytest tests/ -q` 全绿（基线以开实验时 HEAD 为准，只增不减）；既有 `tests/test_simple_waker.py`、`tests/test_fs_source.py` 签名去重路径零回归"
evidence_keys:
  - "pytest_summary:新增 test_notification_fanout/test_work_kinds/test_notification_cleanup 单测全绿 + 全量 pytest -q 0 failed"
  - "实测输出:在临时话题 `e2e-fanout-role-narrowing-drain` 上验证：(1) declared/未 declared 已发言两种 case 均收 wakeable；(2) 非参与者 reviewer 对 contextual kind 不收 wakeable；(3) 非参与者 reviewer 对 obligation kind（experiment.phase_changed）仍收 wakeable；(4) 模拟 7 天前 digest 自动 read 且写审计"
  - "grep 核证:server/services/work_kinds.py 含 `required_role` 字段；notification_fanout.py 白名单每轮重算；notification_cleanup.py 仅清 digest 类 + 写 audit"
dependencies:
  - "话题 reviewer-waker-notification-loop（db031fe0-011b-569c-a729-3b68a867e815）Round 1/2 收口决议：A 收窄扇出+白名单每轮重算（采纳 participant 护栏）；B 服务端按角色过滤（采纳 participant 实现点建议）；D 仅 digest 自清 ≥7 天写审计"
  - "reviewer round2 第 17-22 行硬边界：obligation-wakeable 必须保留给 reviewer 全量 fan-out（A2+A4）"
  - "reviewer round2 第 30-31 行补充：D 自清豁免 `experiment.phase_changed`/`experiment.lifecycle.*` obligation-wakeable，即便 7 天未读（A5）"
  - "participant round1 第 16 行护栏：白名单每轮重算覆盖未 declared 已发言 case（A1）"
  - "既有 83bf610 签名去重语义不动；本实验不引入新的去重路径，仅在角色维度过滤（A8 边界）"
  - "DB 话题与 FS 话题两条路径行为一致（或显式记录差异理由）"
  - "服务端既有：work_kinds.py KINDS registry（已有 required_agent_id，需新增 required_role）；server/api/notifications.py 通知扇出（待改）"
  - "无代码冲突实验：fast-gate-allowlist-inversion、cli-hygiene-batch、host-invoke-observability（与本实验无重叠文件）"
---

# FS 话题事件通知按角色扇出收窄 + digest 自清（reviewer waker 空唤醒根治）

## 背景

话题 `reviewer-waker-notification-loop`（2026-08-29 开）实测：FS 话题事件（`topic.comment.created`/`topic.round_advanced`/`topic.lifecycle.closed`）以 wakeable 类别发给**不在话题 participants 清单的 reviewer**，reviewer 收到无任何可执行动作的 wakeable，30s 后再次被 waker 唤醒形成 token 消耗回路。`83bf610` 已上线的签名去重把回路频率压到 ~6%，但唤醒本身仍是浪费。

Round 1/2 三方（host/participant/reviewer）收口：A 收窄扇出+白名单每轮重算、B 服务端按角色过滤、D 仅 digest 类自清。reviewer round2 第 17-22 行硬边界：**obligation 类 wakeable 必须保留全量 fan-out 给 reviewer**，否则会把 obligation 转 digest 再次形成积压回路（reviewer 是唯一有 `pending_reviews`/`pending_result_reviews` obligation 清理角色的 agent）。

## 定稿决议（Round 2 三方表态合并）

| # | 决议 | 来源 |
|---|------|------|
| D1 A 收窄扇出 + 护栏 | FS 话题事件通知 wakeable 收窄到 `creator ∪ declared ∪ speakers_current_round ∪ speakers_prev_round` 白名单，每轮重算；非白名单角色降级 digest 或不发 | host round1 + participant round1 护栏 + reviewer round2 支持 |
| D2 B 服务端实现点 | work_kinds.py 增 `required_role`；filter 层按角色 + participants 派生白名单；waker 侧兜底仅硬截断（不改 wake signature） | host round1 + participant round2 具体改动点 |
| D3 D 仅 digest 自清 | ≥7 天 digest 类自动 read；obligation 类严禁自动清；写审计日志 | host round1 + participant round1 + reviewer round2 |
| D4 obligation 豁免 | filter 对 obligation kind 全量 fan-out 给 reviewer，**不**受白名单约束——reviewer 永远在 obligation 白名单 | reviewer round2 硬边界 |
| D5 D 豁免 obligation-wakeable | 即便 ≥7 天未读，`experiment.phase_changed(review/result_review)` / `experiment.lifecycle.cancelled/withdrawn` 也**不**被自动 read | reviewer round2 第 30-31 行 |
| D6 不动签名去重 | 维持 `83bf610` 语义；本实验不引入新去重路径，仅在角色维度过滤 | host round1 + reviewer round2 |

## 实施顺序（建议，评审可调）

1. **I1 KINDS registry 增加 `required_role`**（A3+A4）：work_kinds.py 每条 kind 增 `required_role`；obligation kind 标记豁免位（filter 层硬约束）
2. **I2 白名单每轮重算 + 双层 filter**（A1+A2）：server/services/notification_fanout.py（新增）从 topic 元数据派生白名单；filter 路径对 contextual kind 按白名单过滤、对 obligation kind 全量 fan-out
3. **I3 digest 自清服务**（A5+A6）：server/services/notification_cleanup.py（新增）周期任务（建议 24h）扫描 unread digest 类 ≥7 天 → 自动 read + 写 `notification_audit_log`
4. **I4 回归测试**（A7）：test_notification_fanout/test_work_kinds/test_notification_cleanup 单测
5. **I5 实测 evidence**：临时话题 `e2e-fanout-role-narrowing-drain` 验证 declared/未 declared 已发言、非参与者 reviewer 对两类 kind 的不同行为、digest 7 天自清 + 审计
6. **I6 重启验证**：docker compose build api + docker compose up -d + curl 验证通知形状（参考 [feedback_docker_api_rebuild.md]）

## 风险与边界

- D4 obligation 豁免不能误伤 contextual kind（unread_change 仍按白名单过滤）；filter 层先判 `kind.priority == 'obligation'` 再决定是否豁免
- D5 即便 ≥7 天未读 obligation-wakeable 也不自清——digest 自清循环必须先 `category == 'digest'` 再判时间
- A1 白名单每轮重算：从 topic 元数据派生 derived view，不缓存；如果 topic 元数据读取成本高，参考 host-invoke-observability 模式在 SDK 层加缓存
- DB 话题与 FS 话题两条路径行为必须一致；目前 FS 话题是单轨主路径（DB 写路径 v0.13 M58 退役），但 server 仍需读 DB 旧话题派生白名单时保证一致
- 落地涉及 server 改动：验收后 `docker compose build api` + `docker compose up -d` + curl 验证通知形状（参考 [feedback_docker_api_rebuild.md]）
- 不动 `83bf610` 签名去重语义
