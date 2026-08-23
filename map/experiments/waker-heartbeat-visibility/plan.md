---
title: "waker 心跳可见性：双时间戳 + server 侧判定 + 全 persona 展示"
acceptance:
  - "A1 停机检测：停全部 waker、无任何手动 `map work`，超过阈值（Settings 默认 15min）后 `map work` 顶部出现 WARN（验收测试用 Settings 项调短阈值等价验证 + 默认值冒烟各一轮）"
  - "A2 归属区分（降级场景回归）：停 waker、invoked agent 持续手动跑 `map work`（持续刷新 `last_api_seen_at`），WARN 仍然出现——验证告警只看 `last_waker_poll_at`；这正是本话题 host invoke 补位场景的活例"
  - "A3 无误报：waker 正常 idle 空转跨过阈值时长（验收用调短阈值等价）无 WARN"
  - "A4 冷启动：server 重启后无全量 stale 误报窗口（时间戳持久化在 agents 表，重启不丢）"
  - "A5 全 persona 展示：`GET /status` 的 `waker_heartbeats[]` 与 `map work` 顶部展示项目内全部 per-agent waker（agent / persona / last_waker_poll_at / stale），非仅当前 persona；「只挂一个 waker」形态可正确表达（其余 persona 显示 never 状态不 WARN）"
  - "A6 文档：MAP-SIMPLE-WAKER.md 补三章——降级路径（waker 不可用 → host invoke 编排）/ 层次说明（waker-stale WARN 是 stale_open_topics 检测的前置信任条件，两者互补）/ 边界说明（本方案只覆盖「waker 挂、server 活」；server 挂是显性故障无需心跳兜底）"
  - "测试面：server 单测覆盖双时间戳刷新语义（普通调用只刷 api_seen、waker 特征请求刷两者）/ null-never 语义 / stale 判定边界 / /status 响应形状；CLI 单测覆盖 --client waker 透传与 WARN 渲染（stub 响应）；A1–A4 场景级集成验证；fast-gate 全量回归通过"
evidence_keys:
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：waker 轮询请求带特征标记；`map work` 顶部渲染全 persona waker 状态，stale 输出 WARN（A1/A2/A3 场景各一轮，阈值调短等价 + 默认值冒烟）"
  - "server 实测：`GET /status` 返回 `waker_heartbeats[]`（含 stale 字段，判定在 server）；普通 `/agents/me/work` 调用不刷新 `last_waker_poll_at`（A2 归属区分）；server 重启后无全量 stale（A4）"
  - "DB 核证：agents 表新列在 prod DB 实际存在（dogfood 查 schema，不只看 alembic current——防 working-tree checkpoint 式漂移）"
  - "grep 核证：MAP-SIMPLE-WAKER.md 三章节落盘；阈值 Settings 项与 MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES 同构"
dependencies:
  - "话题 waker-heartbeat-visibility（3737a4aa-4695-539c-acf2-67d75e89c14d）Round 2 定稿决议 D1–D6：participant 三条语义风险（归属污染 / 阈值锚定 / 冷启动）与 reviewer 三点修正（双时间戳 / 固定 15min Settings / server 侧判定 + 全 persona 展示）均吸收为验收前提"
  - "无其他 active 实验依赖（本话题无前置实验）"
---

# waker 心跳可见性：双时间戳 + server 侧判定 + 全 persona 展示

## 背景

话题 `waker-heartbeat-visibility`（发起帖 2026-08-23 实测）：simple-waker 是 FS 话题轮次推进后的主唤醒链路，但平台侧**没有任何地方能看到 waker 活着没**——waker 状态只在本地文件，停机时 advance-round 照常生成通知但无人消费，所有等表态话题静默挂起，且无告警。本会话靠 `ps aux` 手动发现后改用 host invoke 补位，「是运气不是机制」。

Round 2 双方（participant + reviewer）均同意开实验且零阻塞，并独立收敛到同一核心风险：**naive endpoint last-seen 有归属污染**——waker 轮询与人工 `map work` 同端点、同 persona token、同查询参数，恰好在降级场景（invoke 补位期间被唤醒 agent 频繁手动跑 `map work`）告警失灵。全部修正已吸收为下述定稿决议。

## 定稿决议（来自话题 Round 2 Summary，D1–D6）

| # | 决议 | 来源 |
|---|------|------|
| D1 | `agents` 表加 `last_api_seen_at`（任意 `/me/work` 刷新）+ `last_waker_poll_at`（仅 waker 特征请求刷新）双时间戳，告警只看后者；waker 子进程带特征标记（`map work --client waker`，CLI 透传 server）；记录点在 `/me/work` handler 内（同事务单列 UPDATE），不放 `get_current_agent` middleware（waker 每 cycle 有 whoami + work 两次触点，middleware 会泛化语义） | participant 风险 1 = reviewer 修正 1 |
| D2 | v1 固定绝对阈值 **15min**（≈3× 默认 idle 周期），server Settings 项（与 `MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES` 同构）；不锚 active/idle interval（周期是 waker 侧 env 可调、server 无从得知；双档自适应下锚 active 会持续误报）；waker 上报 interval 动态判定列 v2 不进首版 | 分歧裁决：采纳 reviewer 形态，覆盖 participant 的 10min 起步下限 |
| D3 | 判定只在 server：`GET /status`（server/api/status.py:19，已鉴权 + 项目隔离）的 `GlobalStatusRead` 加 `waker_heartbeats[]`（agent / persona / last_waker_poll_at / stale，per-agent 粒度）；`map work` 顶部渲染**全部** persona waker 状态，CLI 只渲染不复制判定逻辑 | reviewer 修正 3 = participant 展示粒度建议 |
| D4 | 两时间戳列落 `agents` 表持久化（3 persona × 30s 单列 update，SQLite 无压力），server 重启无误报窗口（优于 warm-up 宽限） | participant 风险 3，采纳持久化路径 |
| D5 | **不**用 `simple-remind:*` inbound_event 当心跳（只在 remind 成功时写，空闲 waker 零足迹，语义是提醒审计非存活） | reviewer 补充 1 |
| D6 | MAP-SIMPLE-WAKER.md 补降级路径（host invoke 编排）+ 层次说明（waker-stale WARN 是 `stale_open_topics` 的前置信任条件）+ 边界说明（只覆盖 waker 挂/server 活半边） | 发起帖期望 3 + reviewer 补充 2 |

**null 语义决策（v1 默认，评审可改）**：`last_waker_poll_at` 为 null（该 agent 的 waker 从未心跳）展示 `never` 状态**不进 WARN**——「只挂 host 一个 waker」的部署形态下其余 persona 不会永久告警；`stale` 仅对「曾有心跳且超阈值」为真。

## 调研事实（Round 2 已核证）

| 事实 | 位置 |
|------|------|
| waker 每 cycle 经 `MapCommandClient.work()` 子进程调 `map --persona <p> work --notification-category wakeable`，落 `GET /agents/me/work`；与人工调用同端点同 token 同参数 | `cli/simple_waker.py:618`、`cli/map_command_client.py:97-98`、`server/api/agents.py:200` |
| server 侧对该轮询零足迹（全 server 无 agent last-seen 记录；escalation_resolver 的 `last_seen_by_agent` 是另一语义） | reviewer 代码核证 |
| 轮询双档自适应：active 30s / idle 300s（`--active-interval` / `--idle-interval`，min 5s/30s，env 可调） | `cli/simple_waker.py:131-132、501-502`、`scripts/start-simple-waker.sh` |
| `simple-remind:*` event 只在 remind 成功时写，空闲 waker 零足迹 | `cli/simple_waker.py:716` 起 |
| `/status` 已鉴权 + 项目隔离，`GlobalStatusRead` 为天然挂点 | `server/api/status.py:19` |
| 当前无降级路径章节（文档已确认） | `docs/MAP-SIMPLE-WAKER.md` |

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| S1 迁移与模型 | `agents` 表加 `last_api_seen_at` / `last_waker_poll_at` 两列（nullable timestamp）+ alembic migration + ORM 字段；执行后 dogfood 核 prod DB schema（防 working-tree checkpoint 式漂移：手动 ALTER 与 alembic 守卫不可反义组合） | `server/models.py`、`alembic/versions/` |
| S2 记录点 | `GET /agents/me/work` handler 内：任意调用刷 `last_api_seen_at`；请求带特征标记（`--client waker` → 请求头或查询参数）加刷 `last_waker_poll_at`；同事务单列 UPDATE，不放 middleware | `server/api/agents.py` |
| S3 判定与暴露 | stale 判定（now − last_waker_poll_at > 阈值；null → never 不 WARN）；`GlobalStatusRead` 加 `waker_heartbeats[]`（agent_id / persona / last_waker_poll_at / stale，per-agent）；新增阈值 Settings 项（默认 15min，与 `MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES` 同构） | `server/api/status.py`、`server/services/`、Settings |
| C1 waker 特征标记 | simple-waker 子进程 `map work` 调用加 `--client waker`；CLI `work` 命令接收并透传 server（人工调用不带此参数） | `cli/simple_waker.py`、`cli/commands/`、`cli/map_command_client.py` |
| C2 渲染 | `map work` 顶部渲染全 persona waker 状态（来自 server 算好的字段，CLI 零判定逻辑）；stale 输出 `[WARN] waker heartbeat stale for <agent>(<persona>)`，逐条列出全部 stale；never 显示状态行不告警 | `cli/commands/` |
| D 文档 | MAP-SIMPLE-WAKER.md 三章节（D6）；如触及 SDK 请求模型，UUID 字段序列化必须 `model_dump(mode="json")` | `docs/MAP-SIMPLE-WAKER.md` |
| T 测试 | 见 acceptance 末条：server 单测（刷新语义 / null / stale 边界 / 形状）、CLI 单测（透传 / 渲染）、A1–A4 集成（Settings 调短阈值等价）、fast-gate 回归 | `tests/` |

## 实现顺序

1. S1 迁移 + S2 记录点（先有数据才有判定）→ server 单测刷新语义
2. S3 Settings + 判定 + `/status` 暴露 → server 单测形状与边界
3. C1 waker 标记 → C2 `map work` 渲染 → CLI 单测
4. A1–A4 场景集成验证（调短阈值等价 + 默认值冒烟；改动涉及 server 时 docker compose build api 后再验，不能只 restart）
5. D 文档三章节 + dogfood 核 prod DB schema + fast-gate 全量回归
