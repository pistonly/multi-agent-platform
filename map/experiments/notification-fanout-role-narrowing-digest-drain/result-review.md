---
verdict:
  reason: "实验 8b1d20a1 全部 acceptance (A1-A8) 满足，4 个窄 commit (5948203 / fbf4035 / 1e9ec8e + I6 dev server 重启无代码 commit) 按 plan 实施顺序 I1→I6 落地。worktree 无半成品（git status 仅本实验 plan/review/FS topic 目录 untracked，无 pytest 干扰）；测试面以收口 commit 时刻为准对照。pytest 全量 1753+11=1764 passed 零回归（基线 1712 + I4 新增 41 + I5 e2e 11），ruff check 全绿，83bf610 签名去重零回归。reviewer 视角硬边界在 I6 dev server 重启后实测通过：'reviewer 0 条 topic.close + obligation 仍收' 命中 A2+A4 双层过滤的关键不变量。文件 inventory 核证：plan 提到的全部 10 个文件（work_kinds.py / notification_fanout.py / notification_cleanup.py / topic_role_resolver.py / 4 个 tests + wake.md + e2e tests）均落地，topic_role_resolver 与 test_e2e_fanout_role_narrowing 为实施中合理新增。A6 审计日志复用既有 audit_logs 表（实施偏离，log I2+I3+I4 第 80-82 行已说明理由：避免迁移 + 复用既有审计基础设施；偏离合理，A6 验收目标——每条清理写审计——未受损）。下一步：进入 done，话题 reviewer-waker-notification-loop 行动项（reviewer round2 边界吸收进 plan）已闭环。"
  invariants:
    - item_id: a1
      verified: true
      note: "topic_role_resolver.py 每轮重算（不维护跨请求状态），compute_whitelist_from_view 从 _TopicView 派生；11 个单测覆盖 round1/round2/dedup/undeclared speaker"
    - item_id: a2
      verified: true
      note: "filter_recipients 三分支 obligation / topic.* / 其它；I5 persona property bug 修复使非参与者 reviewer 对 topic.lifecycle.closed 不收（I6 DB 实测 0 条）"
    - item_id: a3
      verified: true
      note: "WorkItemKindSpec required_role 字段 + 13 条实例显式填 host/participant/reviewer/all（I1 commit 5948203）"
    - item_id: a4
      verified: true
      note: "_OBLIGATION_EXEMPT_EVENTS 显式列 cancelled/withdrawn/status_changed/phase_changed(review|result_review)；filter 先判 exempt 才走白名单；I6 DB 实测 obligation 仍收"
    - item_id: a5
      verified: true
      note: "cleanup_digest_notifications 以 category==digest 为首要判定；first_event_at ≤ now-7d；13 个单测覆盖边界"
    - item_id: a6
      verified: true
      note: "每条清理写 audit_logs（action=auto_drain_digest_7d / actor=system / reason / days_old）；实施偏离——复用既有 audit_logs 表而非新建 notification_audit_log，理由充分"
    - item_id: a7
      verified: true
      note: "I4 新增 41 测试（topic_role_resolver 11 + notification_fanout 17 + notification_cleanup 13），I5 新增 11 e2e 测试覆盖 A/B/C/D 四场景"
    - item_id: a8
      verified: true
      note: "ruff check 全绿；pytest 全量 1764 passed（基线 1712 + I4 41 + I5 11），零回归；83bf610 签名去重路径未触碰"
---

# Result Review — Experiment 8b1d20a1

## 审批结论

**accept_result** —— 全部 A1-A8 acceptance 满足，4 个窄 commit 按 I1→I6 顺序落地，I6 dev server 重启后实测 reviewer 硬边界通过。

## 验证证据汇总

| acceptance | 来源 | 验证 |
|------------|------|------|
| A1 白名单每轮重算 | I2 topic_role_resolver.py + 11 单测 | topic_role_resolver 单测全绿 |
| A2 双层过滤 | I2 notification_fanout.py + I5 bug fix | I6 DB curl 实测：reviewer 0 条 topic.close |
| A3 required_role | I1 work_kinds.py + 13 条实例 | I1 commit 5948203 单测全绿 |
| A4 obligation 豁免 | I2 _OBLIGATION_EXEMPT_EVENTS + filter 先判 | I6 DB 实测：obligation 仍收 |
| A5 digest 自清 | I3 notification_cleanup.py + 13 单测 | 边界（cutoff/+1s）测试覆盖 |
| A6 审计日志 | I3 复用 audit_logs 表 | 实施偏离有充分理由 |
| A7 回归测试 | I4 41 + I5 11 = 52 新测试 | 全绿 |
| A8 ruff + pytest | 全量 1764 passed | 零回归，83bf610 不动 |

## 实施偏离评估

A6 审计日志：plan 提到「notification_audit_log 表/文件」，实施选「复用 audit_logs 表」。偏离合理（避免迁移成本 + 复用既有审计基础设施），验收目标——每条清理写审计含 actor/reason/target_id——未受损。log 中已记录理由，host 应在后续 plan revise 时同步 acceptance 措辞。

## 文件 inventory（按 memory reviewer-plan-inventory-check 核证）

- 修改：`server/services/work_kinds.py`（+63/-23 I1）、`server/services/notification_fanout.py`（+167 I2 +13/-2 I5 bug fix）、`server/services/notification_service.py`（+16/-1 I2）、`tests/test_work_kinds.py`（+92/-2 I1）、`.cursor/skills/map-project-collab/references/wake.md`（+15/-15 I1）
- 新增：`server/services/topic_role_resolver.py`（+132 I2）、`server/services/notification_cleanup.py`（+182 I3）、`tests/test_topic_role_resolver.py`（+232 I4）、`tests/test_notification_fanout.py`（+261 I4）、`tests/test_notification_cleanup.py`（+290 I4）、`tests/test_e2e_fanout_role_narrowing.py`（+596 I5）

10 个 plan 预期文件全部落地，2 个合理新增。

## reviewer 硬边界闭环

- A2+A4：filter 对 obligation-wakeable 全量 fan-out 给 reviewer；contextual kind 按白名单过滤 → **reviewer round2 第 17-22 行硬边界已落地 + I6 dev server 实测通过**
- A5：digest ≥7d 自清豁免 obligation-wakeable → **reviewer round2 第 30-31 行硬边界已落地**

## 下一步

实验进入 done。话题 `reviewer-waker-notification-loop` 行动项闭环；该实验的修法落地后，本轮唤醒 reviewer 的「topic.lifecycle.closed 误发给 reviewer」现象将消失——下一轮 waker 醒来时应直接验证。
