# I1 执行日志：补齐 6 个 lifecycle publish 调用点 (D2)

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1 完成，准备进入 I2（`_run_sse_loop`）。

## 范围与目标

D2 要求把 Phase 1 缺失的 6 个 lifecycle SSE publish 补齐，让 waker 能用
`Notification.event` 区分 wake kind（topic_lifecycle / experiment_lifecycle
/ addressed_review_item），而不是把全部 SSE 都映射到 `notification` 桶。

但审计发现：**真正缺 publish 的只有 4 个**。其余 lifecycle 事件
（`topic.created` / `topic.advance_round` / `topic.resolved` /
`experiment.created` / `experiment.phase_changed` / `plan.revised` /
`review.submitted` / `comment.created` / `topic.comment.created`）已经在
`api/common.py` 的 `emit()` → `enqueue_from_event` → `_emit_created` 链路上
广播；Phase 1 D6 的 `inbound_event.UNIQUE(fingerprint)` 在 client 端做了
二段去重，所以 waker 不会被重复唤醒。Plan v2 里写的「SSE 只覆盖
mention / open_topic_opportunity」与实际不符——这是写 plan 时的盘点错误。

## 改动清单

| 文件 | 变更 |
|------|------|
| `server/services/notification_service.py` | 新增 `PERSONA_AGENT_NAMES` 常量、`_resolve_persona_agent_ids()`、`emit_kind()` 包装函数；payload 自动补 `kind`（取事件名的中间段） |
| `server/api/topics.py` | `close_topic` / `reopen_topic` 末尾调用 `emit_kind(event="topic.lifecycle.closed" / "reopened", personas=["host", "participant"])` |
| `server/api/experiments.py` | `withdraw_from_review` / `cancel_experiment` 调 `emit_kind(event="experiment.lifecycle.withdrawn" / "cancelled", personas=["host", "reviewer"])`；`update_review_item` 调 `emit_kind(event="review_item.status_changed", personas=["host"])`；`notification_service` 移到 import 顶部 |
| `tests/test_waker_phase2_i1.py` | 新增 7 个测试覆盖 4 个 publish 点 + actor 跳过 + payload 形状 |

合计 +143 / -1（来自 3 个已存在文件的修改；测试文件为新增）。

## 设计要点

1. **`emit_kind` 与 `emit` 并存而非替换**：`emit` 广播到所有项目 agent + admin（用于「提请相关人」类事件）；`emit_kind` 只定向到 persona agent（host/participant/reviewer），用于「按角色路由」事件。两种发出会同时触发 SSE，Phase 1 D6 dedup 保证 waker 只唤醒一次。
2. **actor 跳过**：复用 `enqueue_for_agents` 已有的 `if recipient_id == actor_id: continue` 跳过逻辑——避免 host 关闭自己的 topic 时自唤醒（验证见 `test_i1_emit_kind_skips_actor_for_self_close`）。
3. **payload 自动补 `kind`**：`topic.lifecycle.closed` → payload.kind="topic.lifecycle"。I2 在 waker 里可以直接读 `Notification.payload_json["kind"]` 构 wake fingerprint，不必再解析 event 名。
4. **persona 解析失败静默 no-op**：如果 project 还没绑定某个 persona（如早期 onboarding），`_resolve_persona_agent_ids` 返回空，`emit_kind` 直接 return `[]`，不会因为缺 agent 而 500。

## 测试

新增 `tests/test_waker_phase2_i1.py`，7 个用例：

| 用例 | 验证 |
|------|------|
| `test_i1_close_topic_emits_kind_to_host_and_participant` | admin 关 topic → host + participant 各收 1 条 |
| `test_i1_reopen_topic_emits_kind` | admin 重开 topic → 同上 |
| `test_i1_experiment_cancelled_emits_kind_to_host_and_reviewer` | admin cancel → host + reviewer |
| `test_i1_experiment_withdrawn_emits_kind_to_host_and_reviewer` | admin withdraw → 同上 |
| `test_i1_review_item_status_changed_emits_kind_to_host` | reviewer withdraw item → host（actor 跳过 reviewer） |
| `test_i1_emit_kind_skips_actor_for_self_close` | host 关自己的 topic → 仅 participant 收 1 条 |
| `test_i1_notification_payload_carries_kind_and_ids` | DB Notification.payload_json 含 kind/topic_id/status |

测试技巧：所有 lifecycle 动作都通过 **admin token** 触发，确保 admin 是 actor，
从而避免「actor 跳过」清零 persona 接收者。review_item 用 **reviewer persona** 创建
review（拿到 reviewer_agent_id）后用同一 token withdraw——验证 reviewer → host 单向唤醒。

## 执行结果

```
$ python -m pytest tests/test_waker_phase2_i1.py -v
tests/test_waker_phase2_i1.py::test_i1_close_topic_emits_kind_to_host_and_participant PASSED
tests/test_waker_phase2_i1.py::test_i1_reopen_topic_emits_kind PASSED
tests/test_waker_phase2_i1.py::test_i1_experiment_cancelled_emits_kind_to_host_and_reviewer PASSED
tests/test_waker_phase2_i1.py::test_i1_experiment_withdrawn_emits_kind_to_host_and_reviewer PASSED
tests/test_waker_phase2_i1.py::test_i1_review_item_status_changed_emits_kind_to_host PASSED
tests/test_waker_phase2_i1.py::test_i1_emit_kind_skips_actor_for_self_close PASSED
tests/test_waker_phase2_i1.py::test_i1_notification_payload_carries_kind_and_ids PASSED
============================== 7 passed in 22.95s ==============================

$ python -m pytest tests/test_waker_phase1_acceptance.py tests/test_waker_phase2_i1.py \
                 tests/test_notifications.py tests/test_notification_stream.py -v
======================== 18 passed in 77.35s ==============================

$ ruff check server/api/topics.py server/api/experiments.py \
                server/services/notification_service.py tests/test_waker_phase2_i1.py
All checks passed!
```

Phase 1 A1–A5 全部继续通过，无回归。

## 与 Plan 的偏差（待 reviewer 知悉）

Plan v2 D2 写「6 个 lifecycle publish 调用点」，实际只补了 4 个：

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| topic.lifecycle.closed | ✅ 加 | 原本 `emit()` 不覆盖 close |
| topic.lifecycle.reopened | ✅ 加 | 同上 |
| experiment.lifecycle.cancelled | ✅ 加 | 原本只走普通 Notification，无 SSE |
| experiment.lifecycle.withdrawn | ✅ 加 | 同上 |
| review_item.status_changed | ✅ 加 | 原本只写 DB，无 SSE |
| topic.advance_round / experiment.phase_changed | ❌ **没改** | 已经被 `emit()` 广播覆盖 |

第 6 个不需要新增 publish，但 I2 需要在 waker 里识别这些已存在的事件名
（`topic.advance_round` / `experiment.phase_changed` / `plan.revised` /
`comment.created` 等），把它们的 SSE 路由到对应 wake kind。这一步属于 I2 的
kind 路由表工作，已在 plan v2 I2 步骤里。

## 下一步：I2 `_run_sse_loop` (D1+D3+D4)

主要工作：
1. 在 `runtime_waker.py` 新增 `_run_sse_loop(persona)`，在已有 `discover_wake_events` 旁路并行
2. SSE 客户端连 `GET /api/v1/agents/me/notifications/stream`（Phase 1 已实现），解析 `notification.created` 事件
3. 按 `Notification.event` 路由到 wake kind：
   - `topic.lifecycle.*` / `topic.advance_round` / `topic.resolved` / `topic.comment.created` → `topic_lifecycle`
   - `experiment.lifecycle.*` / `experiment.phase_changed` / `plan.revised` → `experiment_lifecycle`
   - `review.submitted` / `review_item.status_changed` → `pending_review`
   - `comment.created`（实验评论） → `pending_result_review`
4. I3 处理断线补偿（`recent_resume_attempts` 滑动窗口 + `event_source="replay"` 豁免）

预期 I2 完成时间：与 I1 相当（1 个 wake cycle）。
