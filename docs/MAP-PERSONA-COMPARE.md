# MAP Persona Compare（`--persona-compare`）

`map experiment status --persona-compare` 单命令对比同一实验在
**host / reviewer / participant** 三个 persona 视角下的视图差异。
这是实验 `0db51e10`（跨 persona acceptance_status 对比命令）的
schema baseline 文档：每个 per-agent partition 在三个 persona 下应
呈现什么形状、跨 persona diff 怎么判定、何时折叠。

## 适用范围

- **phase**:任意 phase；其他 phase 的 acceptance_status 等 partition
  可能因 `hidden_for_current_persona=True` 而折叠（plan v2 (a) +
  I3(5d) phase whitelist）。
- **persona**:当前项目已 bootstrap 的所有 agent（默认 `sorted(config.tokens.keys())`），
  或 `--for-personas host,reviewer` 显式选人。
- **actor**:任何已认证 agent 通过 `map --persona <self> experiment status
  --persona-compare <id>` 调用；不要求 admin。**显式越权**（用其他
  persona 的 token 跑）会被 `156468d0` 错误码拒绝（plan v2 (c) 跨引
  `docs/MAP-ERROR-CODES.md`）。

## 命令形态

```bash
# 默认：项目所有 persona 对比（diff 表格）
map experiment status --id <uuid> --persona-compare

# 显式选人
map experiment status --id <uuid> --persona-compare \
  --for-personas host,reviewer

# 全字段快照（raw YAML；调试 + 快照测试用）
map experiment status --id <uuid> --persona-compare --raw
```

输出两段：

1. diff 表格（4 字段：`actions` / `blocked_on` / `phase_owner` /
   `informational_only`）
2. `acceptance_status partition: <bucket>` 单行（见下 §四 partition 分类）

## per-agent partition 视图

下表列出**同一实验在同一时刻**三个 persona 应看到的 partition 形状
（plan v2 5b schema baseline）。partition 列值用 `"schema_field:
value"` 简写；完整定义见 `sdk/python/map_types/schemas.py`。

### 1. `acceptance_status`

| Persona | 典型可见性 |
|---------|-----------|
| host (creator) | 看到完整 `acceptance_status[]` + 每个 entry 的 `evidence_provided` / `reviewer_verdict` |
| reviewer (non-creator) | 同 host 视角，但 `reviewer_verdict` 是自己写的那条（其他 reviewer 的 verdict 也可见） |
| participant | 同 host 视角（接受 `reviewer_verdict` 但不能写 verdict） |

**跨 persona diff 触发条件**：
- 任一 entry 的 `id` / `evidence_provided` / `reviewer_verdict`
  三元组在 persona 间不等。

### 2. `todos`

| Persona | 典型可见性 |
|---------|-----------|
| host | 看到 `my_open_experiments`（creator = self）+ 不看到 `pending_reviews` + 看到关联 topic `pending_topic_replies` |
| reviewer | 看到 `pending_reviews`（self 未评过的）+ 看到 `pending_result_reviews`（host complete 后） |
| participant | 通常 `my_open_experiments` 为空（不是 creator），看到关联 topic `pending_topic_replies` |

### 3. `wakeable_notifications`

| Persona | 典型可见性 |
|---------|-----------|
| host | `@mention` + `experiment.lock.no_progress` + `experiment_lifecycle` + `action_item` escalate |
| reviewer | `pending_reviews` 提醒 + `pending_result_reviews` + 关联 topic mention |
| participant | 关联 topic `pending_topic_replies` + mention |

### 4. `my_open_experiments`

| Persona | 典型可见性 |
|---------|-----------|
| host | 该实验在此列表中（creator = self） |
| reviewer | **不在**此列表（仅 creator 视角） |
| participant | **不在**此列表（仅 creator 视角） |

> 这意味着 `--persona-compare` 在 `my_open_experiments` 维度上 host
> vs reviewer/participant 是「结构性不一致」（不属于业务 diff，
> 而是 persona 权限分区的语义差异）。

## 四 partition 分类（`acceptance_status`）

CLI 在 `_persona_compare_view` 末尾输出 `acceptance_status partition:
<bucket>`。分类由 `_classify_persona_compare_partition(views)`
纯函数计算（`cli/main.py`）。

| Bucket | 含义 | 判定 |
|--------|------|------|
| `all_agree` | 所有 persona 看到相同的 acceptance_status 集合 | `distinct_signatures == 1` 或 `len(views) == 1` |
| `partial_diff` | 至少两个 persona 相同，但不是全部 | `1 < len(distinct) < len(views)` |
| `full_diff` | 每个 persona 集合两两 disjoint | `len(distinct) == len(views)` |
| `cross_phase_fold` | 任一 persona `hidden_for_current_persona=True` | 折叠优先于 diff |

签名 key = `(id, evidence_provided, reviewer_verdict)` 三元组的
`frozenset`。两条 `id=a1, verdict=accept` 与 `id=a1, verdict=reject`
判为 `full_diff`（不是 `all_agree`），因为 reviewer 关心 verdict
是否一致。

## audit 字段

每次 `--persona-compare` 调用写一条 `kind=cross_persona_call` audit：

| 字段 | 来源 | 示例 |
|------|------|------|
| `caller_agent_id` | 当前 persona agent id | `8ab78cfc-...` |
| `target_experiment_id` | `--id` 实验 uuid | `0db51e10-...` |
| `timestamp` | 服务端写入时间 | ISO 8601 |
| `visibility_diff` | 5 键 dict：`actions` / `blocked_on` / `phase_owner` / `informational_only` / `acceptance_status_partition` | `{"actions": {"host": ["complete"], "reviewer": []}, ...}` |
| `result_partition_count` | 实际 fetch 成功的 persona 数 | `2` |
| `diff_size` | 4 字段中跨 persona 不一致的字段数 | `4` |

存储沿用现有 `audit_log` 表（`payload_json` JSON 列），不新建
`cross_persona_call` 表。admin 端查询：

```bash
# 列出该实验所有跨 persona 调用记录
map audit list --kind cross_persona_call --target 0db51e10-...

# 按 partition / diff_size 聚合（R6 metrics 联动后可用）
map audit aggregate --by acceptance_status_partition,diff_size \
  --target 0db51e10-...
```

## 错误码

| 错误码 | 触发 | 处理 |
|--------|------|------|
| `156468d0` | 显式 `--for-personas` 选了无 token 的 persona / 越权调用 | 父实验 `fb8b0e2a` 定义的 contract，本实验跨引 reference，不重新定义 |
| `audit_write_failed` | 服务端 audit 写失败 | CLI 在 stderr 打印 `[warn] audit write failed: <exc>`，**不**中断表格输出（best-effort） |
| `no_map_dir` | `.map/agents.local.yaml` 缺失 | CLI exit 2 + 友好提示 |

## 关联

- 实验 plan: `0db51e10` current_plan_version 2 (revise v2)
- 父错误码实验: `fb8b0e2a`（CLI 输出格式统一 + 错误结构化 P1）
- 跨 persona audit: `server/services/audit_service.py::log_cross_persona_call_no_commit`
- SDK endpoint: `MAPClient.record_cross_persona_call`
- 折叠语义: `ExperimentSummaryRead.hidden_for_current_persona` (I3(5d))
- active agent N=7 窗口: `list_active_personas`（I3(5f)，CLI 端默认选人接入留待 W+）
