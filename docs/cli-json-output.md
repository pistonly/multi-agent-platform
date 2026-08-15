# CLI JSON 输出契约（`--format json`）

> v0.12 M54C（实验 `m54-machine-readable-cli`）。本文是 `map --format json`
> 机器输出的**权威契约**：信封结构、通道（stdout/stderr）、退出码、payload
> 形状与序列化约定。护栏测试 `tests/test_cli_json_schema.py` 用冻结字面量
> 锁定本契约——任何漂移（字段改名、结构变化）会在 CI 失败而不是悄悄破坏
> 脚本消费方。
>
> 输入侧 schema（CLI 接受的 YAML 文件）见 [cli-schemas.md](./cli-schemas.md)。

## 信封

### 成功（stdout，exit 0）

```json
{"ok": true, "data": <payload>}
```

- `data` 的形状由命令返回的 map_types 模型决定（见下表）
- stdout **只含这一份 JSON**，无人类提示行、无前导文本
- 所有告警（弃用提醒、格式覆盖提示等）走 stderr

### 错误（stderr，exit != 0）

```json
{"ok": false, "error": {"error_code": "...", "message": "...", "hint": null, "docs_url": null, "retryable": null, "recovery_command": null}}
```

- 字段集 = `cli/main.py:CLIErrorEnvelope`（`extra="forbid"`）：除 `message`
  外全部可选，缺失时为 `null`（消费方可用 `obj.get(...)`）
- MAP API 错误 exit 1；参数/用法错误（typer usage error）exit 2
- json 模式下 stderr 只含信封，不含人类可读行

### 消费范式

```bash
out=$(map --persona host experiment show --id f4ef8cb2 --format json 2>err)
echo "$out" | jq -r '.data.phase'          # 成功路径
jq -r '.error.hint // .error.message' err  # 失败路径
```

## `data` payload 形状（experiment 族）

| 命令 | `data` 形状 | 权威 schema |
|------|-------------|-------------|
| `experiment show --id <id>` | `ExperimentDetailRead` 对象 | `map_types/schemas/experiment.py` |
| `experiment status --id <id>` | `ExperimentDetailRead` 对象（v0.12 M54C 起 json 模式不再输出人类提示行；`actions` / `blocked_on` / `phase_owner` 在 `data` 内） | 同上 |
| `experiment list` | `ExperimentSummaryRead` 数组 | 同上 |
| `experiment logs --id <id>` | `ExperimentLogRead` 数组 | 同上 |
| `experiment log --id <id> ...` | `LogCreateResponse` 对象：`{"log": ExperimentLogRead, "validation": EvidenceValidationSchema}` | 同上 |
| `experiment pre-complete --id <id> --metadata <yaml>` | CLI 合成视图：`{"experiment_id", "phase", "current_plan_version", "evidence_keys"}`（v0.12 M54C 起不再内嵌 `ok`——信封已携带） | CLI 端合成，非 map_types |
| 其他写命令（start / complete / accept-result / ...） | 命令返回的 map_types 模型（`model_dump(mode="json")` 结果） | 对应 schema |

topic / agent 等其他命令族同理：`data` = SDK 返回模型经 `model_dump(mode="json")`
序列化的结果，字段名与 REST API 响应体一致（同一 pydantic 模型，无别名差异）。

## 序列化约定

| 类型 | 形式 | 示例 |
|------|------|------|
| UUID | 完整 36 位小写连字符串 | `f4ef8cb2-a9af-46d1-9259-ca728fd33428` |
| datetime | ISO8601，服务端按 UTC 存储输出 naive 形态（无 `Z` 后缀）；输入侧 `Z` / `±hh:mm` 偏移同样接受 | `2026-08-15T11:47:33` |
| enum | 成员值字符串 | `"running"` |
| 缺失可选字段 | `null`（保留键） | `"description": null` |

## 常用 jq 配方

```bash
# 所有实验 id（M54 短 id 的来源）
map --persona host experiment list --format json | jq -r '.data[].id'

# 单实验阶段
map experiment show --id f4ef8cb2 --format json | jq -r '.data.phase'

# 当前可执行动作（status 的机器等价物）
map experiment status --id f4ef8cb2 --format json | jq -c '.data.actions'

# 实验日志摘要
map experiment logs --id f4ef8cb2 --format json | jq -r '.data[].summary'

# review 条目（log-r0 E1 场景：无需再「递归 walk 找 items」）
map experiment review list --id f4ef8cb2 --format json \
  | jq -c '.data[0].items[] | {item_id, reasonable}'
```

## 漂移防护

- `tests/test_cli_json_schema.py` 用**手工冻结的 REST payload 字面量**（不经
  `model_dump` 自产自销）驱动 stub transport → CLI → `json.loads` →
  `map_types` `model_validate` 往返：schema 改名/改型即测试失败
- 错误信封由 `CLIErrorEnvelope`（`extra="forbid"`）在 CLI 边界校验
- 新增命令若返回非 map_types 模型，请在本文「payload 形状」表登记并补
  对应往返测试

## 变更记录

- **v0.12 M54C**：立约。`pre-complete` 移除 `data.ok`（与信封冲突）；
  `status` json 模式不再向 stdout 输出人类提示行（此前 `jq` 首行即失败）
- **v0.12 M54A**：`--format` 可用于任意子命令位置（见根 README）
- **v0.12 M54B**：`--id` 接受 ≥8 位 hex 短前缀（`docs/cli-schemas.md`）
