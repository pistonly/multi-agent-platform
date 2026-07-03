# Current Status — multi-agents-platform

> 本文件为 **status_md 叙事参考**（v10）。清单类数据以 `get_project_status` 快照字段为准。

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agents-platform` |
| workspace_path | `/home/AI02/Documents/quantaeye/multi_agents_platform` |
| 主分支 | `main` |
| 最新版本 | **v0.8+ waker Phase 1 已验收**（polling 基础设施 + TTL sweep + creator filter CLI） |

## Agent 身份与协作路径

本仓库 **统一使用 `.map/` persona + `map` CLI + runtime-waker**。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agents-platform-host` | 主持话题、创建实验、推进生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划 |

**标准路径**：`./scripts/start-all-wakers.sh` → Agent 读 Skill → `map --persona <name>` CLI。

**Runtime 后端**（`MAP_RUNTIME_BACKEND`）：`claude`（默认）、`codex`、`cursor`（Cursor SDK 本地 agent）。详见 [MAP-RUNTIME-WAKER.md](../docs/MAP-RUNTIME-WAKER.md)。

**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`、runner JSON 契约。

## v0.8 闭环验收（实验 `c9776cb4`，plan v3）

| 子项 | 摘要 |
|------|------|
| I1 | 删除 `ROUND_SUMMARY_RE`，轮次门禁改读 `round_summary_count` |
| I3 | `scripts/systemd/map-wakers.service` + install.sh + 测试 |
| I4 | advance-round ack（24h silence=consent、reject 409、topic-host Skill） |
| SSE A/C | `test_sse_isolation.py` + `test_sse_schema_overlap.py` |
| P3 dogfood | 探针 `90cba1c3` draft→done；三 waker `wake_errors: 0`；未启动 bridge |

C 类保留正则清单见 [MAP-RUNTIME-WAKER.md](../docs/MAP-RUNTIME-WAKER.md#v08-保留正则不在-round_summary_re-清理范围)。

## waker Phase 1 验收（实验 `a188555c`，plan v2）

话题 `cfd1578e`（轮询 waker 改 hook）Phase 1 polling-only 路径，为 Phase 2 SSE 叠加铺平幂等与审计语义。

| 交付 | 摘要 |
|------|------|
| D1 | `inbound_event` 表 + migration（`agent_id` FK、`UNIQUE(fingerprint)`） |
| D2 | todos 客户端 `created_at` 游标过滤（服务端 `since` 留 Phase 2） |
| D3 | fingerprint 写盘先于 resume（客户端第二道闸） |
| D6 | `POST /me/inbound-events` record 端点 + waker resume 前调用（服务端主闸） |
| D5 | sessions jsonl 补 `event_source` + `event_id` + `fingerprint`（A3 join key） |
| A1–A5 | 重放拒绝、并发去重、三表 join、P95 基线、source=polling 全部通过 |

## 近期交付（7/2）

| 实验 | 内容 |
|------|------|
| `4459f047` | runtime-waker state events **TTL sweep**（孤儿 entry 清理、`events_pruned`、`--no-prune-events`） |
| `c572a725` | **`map topic list --creator <name\|id>`**（CLI name→id 解析，服务端零改动，8 条验收通过） |

## 已完成功能（里程碑续）

| 阶段 | 内容 |
|------|------|
| M25–M26 | v0.7 P1 轮次字段 + P2 host bridge（**legacy，不再扩展**） |
| M27 | v0.8 runtime-waker 标准路径、systemd 部署、ack 门禁、SSE acceptance |
| M28 | waker Phase 1 基础设施（inbound_event / fingerprint 主闸+二闸 / 审计 join） |
| M29 | waker TTL sweep + CLI topic list `--creator` |
| M30A | 通知分类（`category` wakeable/digest）+ `fingerprint_version` v1/v2 + `group_key` + `event_count` + 时间戳链 + API/SDK/CLI/Web 全链路 `category` 过滤参数 |
| M31 | waker 降噪接线：runtime_waker 只消费 `category=wakeable`；SSE 帧 payload 带 category/wake_version/fingerprint_version；v1 fingerprint 走 `inbound_events.rejection_count` 路径（waker 不 resume、audit 保留） |

## 技术栈

- **后端**：FastAPI + SQLAlchemy + Alembic
- **前端**：React + Vite + TypeScript
- **协作**：CLI (`map`)、runtime-waker（`claude` / `codex` / `cursor` 后端）、`.cursor/skills/`
- **部署**：Docker Compose（API :8001 / Web :3000）；可选 systemd `map-wakers.service`

## 阻塞 / 风险

- SSE 仍为单进程 pub/sub；多 API 副本需外部扇出
- `cli/host_worker` 仍留于仓库作 legacy，计划单独实验删除
- `tests/test_experiment_lock.py` 与 integration 同名模块存在 pytest 收集冲突（`--ignore` 规避）

## 下一步（建议）

1. **M30A+M31 实验验收完成**（实验 `3d46e2bb`：通知分类 + waker 降噪接线；I1–I5 全过，提交 reviewer 审批）
   - v0.9 采用 `WAKEABLE_NOTIFICATION_EVENTS` 显式白名单作为 feature flag 等价（默认 `digest` + 显式 wakeable 允许列表），不引入运行时配置开关
   - 端到端 dogfood 阶段发现并修复：(a) prod DB `notifications.fingerprint_version` 列缺失（working-tree checkpoint 手动 ALTER 后 alembic `_has_column` 守卫跳过 → 已补 ALTER + 索引）；(b) Web `types.ts` Notification 缺字段（plan I4 §风险段声称已加但实际未加 → 已补 + tsc 通过）
2. **M32 对象级聚合（另起话题）**（话题 `d0df651c` action_item：group_key schema 精细化 + 「同一对象」边界条件 + 与 SSE 帧 payload 兼容方案；M30A+M31 done 后发起）
3. **Phase 2 SSE 叠加实验**（话题 `cfd1578e` action_item：SSE 长连 + lifecycle publish + 重连补偿；Phase 1 前置已满足）
4. 关闭 creator filter action_item（实验 `c572a725` 已 done，待 `topic resolve` 同步）
5. 删除 legacy host bridge / runner 脚本（单独 issue）
6. v0.9 Codex 探针；修复 pytest 模块名冲突；SSE B 模块闭包检查；v1 `rejection_count` 监控阈值观察（M30A+M31 后续观察项）
7. CI 持续：pytest + vitest + alembic upgrade

## 关键文档

- [README](../README.md)
- [PRD v0.9 草案：waker Phase 2 通知降噪](../docs/PRD-v0.9.md)
- [MAP-RUNTIME-WAKER.md](../docs/MAP-RUNTIME-WAKER.md)
- [AGENTS.md](../AGENTS.md)
