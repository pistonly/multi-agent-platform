# v0.8 实验 I3 + I4 + SSE A/C 执行日志

实验：`c9776cb4-11f6-406e-baae-de3f259ef11d`（plan v3）  
执行人：multi-agents-platform-host  
时间：2026-07-01

## I3 — systemd waker unit

| 文件 | 说明 |
|------|------|
| `scripts/systemd/map-wakers.service` | unit 模板，`ExecStart` → `start-all-wakers.sh` |
| `scripts/systemd/map-wakers.service.install.sh` | install / uninstall / dry-run；无 systemd 或权限不足时非零退出 |
| `docs/MAP-RUNTIME-WAKER.md` | 增补「生产 systemd 部署」章节 |
| `tests/test_systemd_install.py` | 5 场景 acceptance |

验证：

```bash
bash scripts/systemd/map-wakers.service.install.sh --dry-run
pytest tests/test_systemd_install.py -q   # 5 passed
```

## I4 — advance-round ack（waker 路径）

| 文件 | 说明 |
|------|------|
| `alembic/versions/020_topic_advance_round_pending_since.py` | `Topic.advance_round_pending_since` |
| `server/domain/topic_ack_constants.py` | 24h 超时常量 + ack 标记 |
| `server/services/topic_ack_service.py` | 动态 ack 集合、reject/timeout/pending |
| `server/services/topic_service.py` | `advance_topic_round` + `record_participant_round_ack` |
| `server/api/topics.py` | host `--ack-ids` / participant `--ack` 双路径 |
| `cli/main.py` | `map topic advance-round --ack-ids` / `--ack` |
| `sdk/python/map_types/schemas.py` | `TopicAdvanceRound` 扩展 |
| `.cursor/skills/topic-host/SKILL.md` | ack 收集与 advance 说明 |

验证：

```bash
pytest tests/test_topics.py -k ack -q   # 8 passed
```

## SSE A + SSE C

| 文件 | 说明 |
|------|------|
| `server/services/sse_event_schemas.py` | SSE 传输层 schema 定义 |
| `tests/test_sse_isolation.py` | AST import 白名单（`notification_stream.py`） |
| `tests/test_sse_schema_overlap.py` | 字段与 service 模型并集检查 |
| `tests/test_sse_acceptance.py` | 正向 + 反向 pytest 包装 |

验证：

```bash
python tests/test_sse_isolation.py      # OK
python tests/test_sse_schema_overlap.py # OK
pytest tests/test_sse_acceptance.py -q  # 4 passed
```

## 平台附带

- `server/services/plan_service.py`：`running` phase 可 `plan revise`（v3 修订时已用）
- `server/main.py`：409 响应附带 `reason` 字段（ack_rejected / ack_pending / archived_topic）

## 回归

```bash
pytest tests/test_topics.py tests/test_systemd_install.py tests/test_sse_acceptance.py \
  tests/test_runtime_waker.py tests/test_host_worker.py -q
# 71+ passed（本 log 撰写时）
```

## 待办（P3 / 完成条件）

- [ ] waker dogfood 全链路（draft → complete，未启动 bridge）
- [ ] status_md v9 写入 v0.8 waker 闭环记录
- [ ] 满足 v3 四条完成定义后 `experiment complete`
