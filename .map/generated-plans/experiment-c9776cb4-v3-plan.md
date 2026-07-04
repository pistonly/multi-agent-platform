# v0.8 闭环实验（waker 路径）

## 目标

收尾 **runtime-waker + Agent 直执行** 主线后的工程化工作：完成 I1（已落地）+ I3 + I4 + SSE A/C，并以 **waker dogfood** 验收闭环。

**架构前提（v3 修订）**：本仓库标准协作路径为 `start-all-wakers.sh` → Agent 读 Skill → `map --persona <name>` CLI。**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`、runner stdin/stdout JSON 契约。v2 中依赖 bridge 的验收项全部移除或改写。

## 来源

- 主持话题：`8caab818-fc05-4ce9-a051-70973503ade3`（讨论接下来的计划）
- Round 2 Summary：`da5d2af8-5fc6-4c1d-8f58-500549987bc6`
- v1 review：`415884ec-4253-4572-bab1-5025ae1f6cdb`（9 open → v2 addressed）
- **v3 修订动因**：团队确认 bridge/runner 废弃，waker 为唯一标准路径；I2 与 bridge dogfood 与主线冲突，收窄 scope

## 范围

| 编号 | 内容 | 状态 | 类别 |
|------|------|------|------|
| **I1** | 删除 `cli/host_worker_topic.py` 的 `ROUND_SUMMARY_RE`，改读 `topic.round_summary_count` | **已完成**（log #1） | legacy fallback 清理 |
| ~~**I2**~~ | ~~runner 解析抽到 `map_runner_io.py`~~ | **取消**（bridge 废弃，不维护 runner） | — |
| **I3** | 生产 systemd unit 包装 `scripts/start-all-wakers.sh` | 待做 | waker 运维 |
| **I4** | advance-round ack（server + CLI；**host 经 topic-host skill / map CLI 调用**，非 host_worker） | 待做 | 协作流程门禁 |
| **SSE A** | `tests/test_sse_isolation.py` AST import 白名单 | 待做 | SSE 隔离 acceptance |
| **SSE C** | SSE 事件 payload schema 与 service 模型并集检查 | 待做 | SSE 隔离 acceptance |

## 不在本实验范围（单独 issue 跟踪）

- **I2 / runner 抽象 / `map_runner_io`**：bridge 路径废弃，不投入
- **Claude/Cursor bridge dogfood**：不作为本实验完成条件
- **host_worker 新功能或长期维护**：仅保留 I1 已做的 fallback 清理；整块删除另开实验
- **SSE B**（模块依赖闭包）：nice-to-have，列 issue
- **Codex bridge 探针**：v0.9，触发条件改为「v0.8 waker 闭环 + waker dogfood」
- **main-bac 旧 bridge 下线**：双写只读 1 周 → 删除（切换清单：runner 路径 / `MAP_AGENT_TOKEN` / 文档）

## 实验完成统一定义

实验 `c9776cb4` 完成判定 = **以下 4 条全部成立**：

1. **I1（已完成）+ I3 + I4 + SSE A + SSE C acceptance 全部通过**（各自测试 + 反向测试 + 全量 `pytest tests/` 全绿）
2. **至少 1 次 waker dogfood 全链路**：本实验或同里程碑内另一实验，经 **runtime-waker 唤醒** 的 host/participant/reviewer 完成 draft → review → approved → running → complete；**全程未启动 host bridge**
3. **waker 稳定性证据**：dogfood 期间三 persona waker state 无异常永久 skip（评审后 fingerprint 变更可唤醒 host 等已知修复已生效）；可选附 `.map/waker-logs/*.log` 摘要
4. **status_md v9 已更新**：C 类保留正则清单（I1 已写入 `MAP-RUNTIME-WAKER.md`）+ v0.8 waker 闭环验收记录

满足后 host 调 `map experiment complete --id c9776cb4`。

## 验收标准

### I1 — ROUND_SUMMARY_RE 删除（已完成）

- [x] `grep -n 'ROUND_SUMMARY_RE' cli/host_worker*.py` 无匹配
- [x] `host_worker_topic.py` 改读 `topic.round_summary_count` / `discussion_round`
- [x] `tests/test_topics.py` + `tests/test_runtime_waker.py` + `tests/test_host_worker.py` 全绿
- **v3 说明**：I1 改的是 legacy `host_worker`；waker 路径本身不依赖该模块。保留 I1 是为减少 legacy 代码混乱，**不要求** bridge dogfood。

### I3 — systemd unit（waker）

- 新文件 `scripts/systemd/map-wakers.service` + `scripts/systemd/map-wakers.service.install.sh`
- `install.sh` 注册 unit、`systemctl enable --now map-wakers`（生产机）
- **新增 `tests/test_systemd_install.py` 5 场景**：
  1. install 成功（mock `systemctl enable`）
  2. `--uninstall` 成功（mock `systemctl disable`）
  3. `--dry-run` 不调用 systemctl
  4. systemd 不可用 → 非零 + stderr，不破坏开发机
  5. 权限不足 → 非零 + stderr，不 sudo 重试
- `docs/MAP-RUNTIME-WAKER.md` 增补「生产 systemd 部署」章节
- 不强制开发机启用 systemd

### I4 — advance-round ack（waker / server 视角）

**字段语义与 CLI / HTTP body**（与 v2 一致）：

- HTTP `acknowledged_by: List[str]`：host 传入的已 ack participant `agent_id` 列表
- `map topic advance-round --ack-ids <uuid>,...`：host 端
- `map topic advance-round --ack={accept|reject}`：participant 端表态（写入 comment 或专用 endpoint）
- server **动态 ack 集合** = topic comments 去重 `author_agent_id`，排除 host、dismiss 的 participant、archived topic
- ack 通过 = `acknowledged_by` ⊇ 动态集合
- `Topic.advance_round_pending_since` + 24h silence=consent
- 显式 reject → `409 reason=ack_rejected`
- 超时常量放 server/cli 常量模块，不 hard-code 散落

**host 执行路径（v3 取代 v2 的 host_worker 调用点）**：

- **topic-host Skill** 描述：发 Round N Summary 后收集 ack → `map topic advance-round --ack-ids ...`
- host Agent 被 `topic_lifecycle` wake 时按 Skill 执行；**不**改 `cli/host_worker*.py` 新逻辑
- 409 `ack_rejected`：host 在 Summary 线程 @ 拒绝者，不强制 advance

**acceptance 测试 `tests/test_topics.py`**（与 v2 相同 8 条）：

1. `test_advance_round_ack_dynamic`
2. `test_ack_timeout_silence_consent`
3. `test_ack_rejected_409`
4. `test_ack_set_excludes_dismissed`
5. `test_ack_set_excludes_archived_topic`
6. `test_host_only_topic_advance`
7. `test_dismiss_after_advance_init`
8. `test_archived_topic_advance_rejected`

**Skill 验收**：`.cursor/skills/topic-host/SKILL.md` 含 ack 收集与 `--ack-ids` 调用说明（可与 I4 同一 PR）

### SSE A — 静态 import 白名单

**前置**：host 决策 SSE handler 是否拆到 `server/api/sse.py`（执行 SSE A 前确定）

- 白名单：`server.services.{topics,experiments,comments,mentions}` + stdlib + `fastapi` / `starlette`
- `tests/test_sse_isolation.py` 退出码 0 为通过
- 反向测试：人为越权 import → CI fail

### SSE C — 事件 schema 不重叠

- 提取 SSE handler 事件 payload schema
- 字段 ⊆ `Topic` / `Experiment` / `Comment` / `Mention` service 模型（只读视图）
- 反向测试：人为 SSE 独有字段 → CI fail

## 风险与回滚

- **I1**：legacy host_worker 路径行为变化；已测。回滚：`git log --grep='map: checkpoint before experiment'`
- **I3**：无 systemd 环境 install 失败 → install.sh 降级，不影响开发
- **I4**：participant 长期不上线 → 24h silence=consent
- **取消 I2**：避免在废弃 runner 上继续投入；若本地有 `map_runner_io` 草稿应回退，不提交

## 执行顺序（v3）

| 阶段 | 任务 | 依赖 |
|------|------|------|
| ✅ | **I1** | 已完成 |
| P1 并行 | **I3** + **I4**（server/CLI/Skill） | 互相独立 |
| P2 并行 | **SSE A** + **SSE C** | P1 后或并行（与 server 改动协调） |
| P3 | **waker dogfood** + status_md v9 | P1 + P2 全绿 |

~~P1.5 I2~~、~~bridge dogfood~~ 已删除。

## status_md v9 落点

- **C 类保留正则**：已在 `docs/MAP-RUNTIME-WAKER.md`（I1）
- **v0.8 waker 闭环记录**：complete 时写入 status_md——含 I3/I4/SSE 验收摘要 + waker dogfood 一次全链路记录

## 验证命令

```bash
pytest tests/ -x

# I1（已完成）
grep -n 'ROUND_SUMMARY_RE' cli/host_worker*.py || echo "OK"
pytest tests/test_topics.py tests/test_runtime_waker.py tests/test_host_worker.py -x

# I3
bash scripts/systemd/map-wakers.service.install.sh --dry-run
pytest tests/test_systemd_install.py -x

# I4
pytest tests/test_topics.py -k ack -x

# SSE
python tests/test_sse_isolation.py
python tests/test_sse_schema_overlap.py

# waker dogfood（人工 + 日志）
# 确认 start-all-wakers.sh 运行，未启动 start-host-bridge*.sh
# 本实验 running → complete 或另开实验走全链路
```

## 后续（不在本实验）

- v0.8 waker 闭环 + dogfood → v0.9 Codex 探针（触发条件改写，不依赖 bridge）
- 删除 `cli/host_worker` + runner 脚本（单独 issue/实验）
- SSE B、main-bac bridge 下线

## 决策记录

- Round 1/2：v0.8 主序列优先
- v2：回应 reviewer 9 条 unreasonable
- **v3（2026-07-01）**：bridge/runner 废弃，收窄为 waker 路径；取消 I2 与 bridge dogfood；I4 host 执行改 topic-host Skill + map CLI；完成条件改为 waker dogfood + waker 稳定性
