# I2+I3 执行日志：SSE 长连主路径 + D3 重连补偿 + D4 客户端限速

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1（lifecycle publish）已完成；本日志覆盖 I2（SSE 主路径）+ I3（重连补偿/限速）。

## 范围与目标

| 子项 | 来源 | 范围 |
|------|------|------|
| **I2 / D1** | plan v2 §D1 | 把 waker 主触发从「轮询」切到「SSE 长连 + 10min 兜底轮询」；复用 Phase 1 `record→mark→resume` 流程，不重复实现 |
| **I2 / kind 路由** | plan v2 I2 步骤 | 按 `Notification.event` 路由到对应 wake kind（与 I1 的 `emit_kind` 链路对齐） |
| **I3 / D3** | plan v2 §D3 | SSE 长连断线时指数退避重连；重连后 `notifications_unread` 全量补读（事件丢失补救） |
| **I3 / D4** | plan v2 §D4 | 客户端 fingerprint 二闸（60s 滑动窗口）；补漏事件携带 `replay=True` 豁免强制 wake |

## 改动清单

| 文件 | 变更 |
|------|------|
| `cli/runtime_waker.py` | 新增 SSE 客户端、解析器、退避、限速、replay 补漏、`_run_sse_loop_async` 主循环。`RuntimeWakerConfig` / `RuntimeWakerStats` 各加 SSE 配置/统计字段；`_wake_event` 接受 `event_source` 参数 |
| `tests/test_waker_phase2_i2_i3.py`（新） | 42 个单元测试，覆盖 parser/backoff/router/rate-limit/build helpers/stats/config |
| `tests/test_waker_phase2_acceptance.py`（新） | 7 个服务端集成测试（A4/A5/A6/A7 复盘）：server-side UNIQUE 主闸、wake 写入 inbound_event、replay 保护 |
| `tests/test_waker_phase2_sse_consumer.py`（新） | 8 个 e2e 测试，使用 `httpx.Response` + 自定义 `aiter_text` 验证：dispatch 一次一帧 / heartbeat 跳过 / split frame 拼接 / replay 豁免 D4 / replay 空 unread / replay 失败吞错 |

合计：cli/runtime_waker.py `2139` 行（新增约 +550），测试 `1106` 行（新增 3 文件）。

## 设计要点

### 1. SSE 主循环（I2 / D1）

新增 `_run_sse_loop_async(stop, stats)`，结构：

```
loop forever (until stop):
    try:
        response = httpx.stream("GET", "/agents/me/notifications/stream", timeout=connect_timeout)
        await _sse_consume_stream(response, stats, stop)   # parse + dispatch
        if stop.is_set(): break
        await _sse_replay_unread(stats)                    # D3 backfill
    except (httpx.RemoteProtocolError, httpx.ReadTimeout, ...) as e:
        delay = sse_backoff_delay(attempt)
        stats.sse_reconnect_total += 1
        await asyncio.sleep(delay)
```

- **与现有轮询并存**：`_run_forever_claude` 现在同时 `asyncio.create_task` SSE 协程，两者把 wake 喂给同一个 `_wake_event` 入口。轮询 10min 兜底保留（plan v2 非目标 #1）。
- **复用 Phase 1 流程**：每条 SSE 帧只触发 `_wake_event(event, event_source="sse")`，内部走 I3 实现的 `record→mark→resume`，**不重复实现**。
- **SSE URL**：`/api/v1/agents/me/notifications/stream`（Phase 1 已落地）。
- **token 复用**：从 `.map/agents.local.yaml` 解析（沿用 Phase 1 I4 的 `_resolve_bearer_token`），不新增配置 surface。
- **stop 语义**：`asyncio.Event` 跨 task 通知；`stop.set()` 后 SSE 协程 await `_sse_consume_stream` 内部循环退出。

### 2. 帧解析器

`parse_sse_frame(buffer: str) -> tuple[list[dict], str]`：

- 按 `\n` 切行；`\r\n` / `\n` 都接受
- 收集 `data: <text>` 行（多行 data 用 `\n` 拼接，spec 规定）
- 跳过以 `:` 开头的注释行（heartbeat）
- 双换行 `\n\n` 触发 frame 闭合
- 部分缓冲（split TCP chunks）合并进下一轮
- 失败行（JSON 解析错）记 warn 但不 raise —— 不能因为单条脏数据断流

返回 `(frames, leftover_buffer)`，避免丢尾部 partial data。

### 3. 事件→kind 路由（I2 步骤）

新增 `_KIND_FROM_PAYLOAD_KIND` 路由表 + `_notification_event_to_wake_kind(notification)`：

| `payload_json.kind` | wake kind |
|---|---|
| `topic.lifecycle` / `topic.comment` / `topic.advance_round` / `topic.resolved` | `topic_lifecycle` |
| `experiment.lifecycle` / `experiment.phase_changed` / `plan.revised` | `experiment_lifecycle` |
| `review.submitted` / `review_item.status_changed` | `pending_review` |
| `comment.created`（实验评论） | `pending_result_review` |
| `mention` | `pending_mention_reply` |
| 其它/未识别 | 静默丢弃（避免噪音；polling 兜底仍能捡到） |

匹配 I1 在 `emit_kind` payload 写入的 `kind` 字段（取事件名中间段，如 `topic.lifecycle.closed` → `topic.lifecycle`）。

### 4. D3 重连补偿（I3 / D3）

- `sse_backoff_delay(attempt: int) -> float`：纯函数 `min(base * 2^attempt, max) + jitter`，单测友好
- 默认 `base=1.0s, max=30.0s`，可通过 `--sse-backoff-base / --sse-backoff-max` 覆盖
- 重连后调用 `_sse_replay_unread(stats)`：
  1. 拉 `notifications_unread`
  2. 失败 → 早退（下次重连再来）
  3. 空 → 不计入 `sse_replay_runs`（避免 metric 虚胖）
  4. 非空 → 每条 `_wake_event(event, event_source="replay")` 强制唤醒

### 5. D4 客户端限速（I3 / D4）

- `_recent_resume_attempts: dict[fingerprint → datetime]` —— 进程内滑动窗口
- `_should_skip_due_to_rate_limit(event)`：
  - 60s 内同 fingerprint 已唤醒过 → 跳过
  - 否则记录 + 通过
- `_prune_recent_resume_attempts()`：每 100 次唤醒做一次惰性清理，避免 dict 无限增长
- **`replay=True` 豁免**：`_wake_event` 入口判断 `event_source == "replay"` → **短路 D4** → 强制唤醒。这是评审项 U2 选 (b) 的落地：补漏事件必须保证到达，不能因客户端限速被误吞；服务端 UNIQUE 主闸（Phase 1 D6）仍防止跨进程重投。
- **`polling` / `sse` 路径**：正常过 D4，避免 SSE+轮询双源对同一 fingerprint 重复 wake。

### 6. `InboundEventSource` 标记

`_wake_event` 入 `inbound_event` 表时携带 `event_source`：

- `polling` —— 来自原 `discover_wake_events`
- `sse` —— 来自 SSE 长连实时帧
- `replay` —— 来自 SSE 重连后补漏

Phase 1 P95 baseline 已预留此字段；sessions jsonl 也用同一值，便于 A1 报表按 source 拆分。

## CLI 表面（不破坏现有）

新增 SSE 启动选项（全部有合理默认）：

```
--sse-enabled / MAP_RUNTIME_SSE_ENABLED          (默认 True)
--sse-recent-resume-window-seconds / MAP_..._WINDOW_SECONDS   (默认 60.0)
--sse-backoff-base-seconds / MAP_..._BACKOFF_BASE_SECONDS     (默认 1.0)
--sse-backoff-max-seconds / MAP_..._BACKOFF_MAX_SECONDS       (默认 30.0)
--sse-read-timeout-seconds / MAP_..._READ_TIMEOUT_SECONDS     (默认 None, 服务端 keepalive 即可)
--sse-replay-limit / MAP_..._REPLAY_LIMIT                     (默认 100)
--sse-connect-timeout-seconds / MAP_..._CONNECT_TIMEOUT_SECONDS (默认 10.0)
```

`--sse-enabled=false` 可回退到 I1 前的轮询主路径（逃生口）。

## 测试

### 单元测试（I2+I3 本地，42 个）

`tests/test_waker_phase2_i2_i3.py`：

| 分组 | 用例 | 覆盖 |
|------|------|------|
| parser | 8 | 单/多/分片 frame、heartbeat、注释行、多 data 拼接、`\r\n` 兼容 |
| backoff | 6 | 序列正确、上限封顶、jitter 范围、`attempt=0` 基础值、负数保护 |
| router | 6 | 6 种 event 名映射、未识别事件、payload.kind 优先、event 缺失回退 |
| rate limit | 8 | 60s 窗口跳过、窗口外允许、惰性清理、100 次触发清理、`replay` 豁免、`sse` 不过豁免 |
| build | 6 | `_build_sse_wake_event` 字段映射、`target_id` 派生、缺失字段降级 |
| stats | 4 | `sse_events_received` / `sse_reconnect_total` / `sse_replay_runs` / `sse_replay_wakes_sent` 累加 |
| config | 4 | CLI 选项 ↔ env 变量双向覆盖 |

### 服务端集成（7 个，A4/A5/A6/A7 复盘）

`tests/test_waker_phase2_acceptance.py`（修正 URL prefix 为 `/agents` 而非 `/me`）：

| 用例 | 验证 |
|------|------|
| `test_sse_url_is_agents_me_notifications_stream` | SSE 客户端 URL 正确 |
| `test_inbound_event_written_on_sse_wake` | SSE → `_wake_event` → `inbound_event` UNIQUE 主闸生效 |
| `test_replay_after_disconnect_dedup_via_unique` | 同一 fingerprint 重连后只入库 1 次 |
| `test_client_rate_limit_skips_recent_fingerprint` | D4 60s 滑动窗口跳过 |
| `test_replay_event_bypasses_client_rate_limit` | D4 豁免（评审 U2） |
| `test_polling_path_also_uses_inbound_event` | polling+sse 双源对同一 fingerprint 去重 |
| `test_lifecycle_event_carries_payload_kind` | I1 `emit_kind` 的 `kind` 字段传到 waker 端 |

### 消费者 e2e（8 个）

`tests/test_waker_phase2_sse_consumer.py`：用 `httpx.Response` + 自定义 `aiter_text` 提供假 SSE 流，验证 `_sse_consume_stream` + `_sse_replay_unread` 的真实异步行为：

| 用例 | 验证 |
|------|------|
| `test_sse_consumer_dispatches_one_wake_per_frame` | 2 帧 → 2 次 `_wake_event`，均 `event_source="sse"` |
| `test_sse_consumer_ignores_heartbeat_and_unknown_types` | heartbeat / ping / unknown type 都不唤醒 |
| `test_sse_consumer_skips_unknown_notification_id` | notifications_unread 已无此 id → 静默跳过 |
| `test_sse_consumer_handles_split_frames` | TCP chunk 切两半仍正确解析 |
| `test_sse_replay_bypasses_d4_rate_limit` | 预填 D4 桶 → replay 仍唤醒（U2 豁免） |
| `test_sse_replay_handles_empty_unread` | 空 unread → 不计入 `sse_replay_runs` |
| `test_sse_replay_swallows_unread_failure` | notifications_unread 抛错 → 不杀 SSE 协程 |
| `test_sse_path_sets_event_source_sse` | 实时帧 `event_source="sse"`（非 replay） |

## 执行结果

```
$ python -m pytest tests/test_waker_phase2_i2_i3.py \
                  tests/test_waker_phase2_acceptance.py \
                  tests/test_waker_phase2_sse_consumer.py -q --no-header
.........................................................                [100%]
57 passed in 67.28s (0:01:07)

$ ruff check cli/runtime_waker.py tests/test_waker_phase2_i2_i3.py \
             tests/test_waker_phase2_acceptance.py tests/test_waker_phase2_sse_consumer.py
All checks passed!
```

合并 Phase 1 acceptance + I1：56 测试无回归。

## 与 Plan 的偏差（待 reviewer 知悉）

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| 「SSE 长连主路径 + 10min 兜底轮询」 | ✅ 与现状并存而非替换 | plan v2 非目标 #1 显式要求保留轮询；按计划落地 |
| D4「与 D3 补漏冲突」解 (b)：补漏豁免 | ✅ `_wake_event` 入口 short-circuit `event_source=="replay"` 强制唤醒 | 评审 U2 已通过；客户端不阻止补漏，服务端 UNIQUE 防跨进程 |
| kind 路由「6 类事件名」 | ✅ 路由表 6 类 + 未识别事件静默丢弃 | 未识别事件靠 polling 兜底；避免噪音唤醒 |

无未达成的 plan 项。

## 已知边界（待 I5 部署 harness 验证）

- **backoff 抖动分布**：单元测试只覆盖单点值；多次连续失败退避序列需 wall-clock 验证（部署 harness 内做）。
- **SSE keepalive**：服务端（`api/notifications.py`）默认 15s 心跳；客户端 `read_timeout` 默认 None 跟随服务端 keepalive。长时间静默 → 服务端可能主动断 → 触发 D3 重连。
- **`--sse-enabled=false` 逃生口**：保留纯轮询模式作为应急回退路径，CLI 已有选项。

## 下一步：I4 文档同步（D5）

- 更新 `.cursor/skills/map-runtime-waker/SKILL.md`：移除 I2 前残留的「未实现 SSE」描述，加 SSE 长连主路径 + D3/D4 行为说明
- 更新 `docs/MAP-RUNTIME-WAKER.md`：把架构图从「轮询」改成「SSE + 兜底轮询」；新增「D4 客户端限速 + 补漏豁免」段落
- 同步 `.cursor/skills/experiment-host/SKILL.md`（如有指代轮询主路径处）
- 不改 `docs/MAP-API.md`（SSE endpoint 已在 Phase 1 落地，文档无变化）
