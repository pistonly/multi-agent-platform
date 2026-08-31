# T5-B I0 spike_note.md — session jsonl 格式考证

## 选样

按 participant round3 §3 强化要求，覆盖「短 / 中 / 长」3 类实验生命周期：
- **T5-A 715202a3**（完整闭环）
- **T2 b3ec2e4d**（短链路）
- **T3 7aeabc2e**（中等链路）

## 关键发现：数据源路径需修订

### 原 plan 假设

```yaml
dependencies:
  - "现有 `.map/runtime-waker-sessions/*.jsonl`（既有 runtime session 流；append-only）"
```

### 实际考证（host 实际查证 2026-08-31）

**`.map/runtime-waker-sessions/*.jsonl` 不含 token usage 字段**：
- 字段结构：`{ts, persona, integration, event, summary, event_source, event_id, fingerprint}`
- 实际内容：waker 唤醒事件（wake / text / tool_use 等）
- **没有 `usage` / `input_tokens` / `output_tokens` 字段**

**真正的 token usage 数据源**：`.map/claude-runtime-home-<persona>/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/<session_id>.jsonl`

字段结构（实测）：
```json
{
  "type": "assistant",
  "message": {
    "id": "chatcmpl-...",
    "model": "claude-sonnet-4-6",
    "usage": {
      "input_tokens": 36159,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "output_tokens": 428,
      "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
      "service_tier": "standard",
      "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}
    },
    "stop_reason": "tool_use"
  },
  "uuid": "ff78ac32-...",
  "timestamp": "2026-07-07T15:54:19.840Z",
  "sessionId": "00084f1a-7cfe-4a84-9ea9-5c7c0ff10b3e",
  "cwd": "/home/AI02/Documents/quantaeye/multi_agents_platform",
  "version": "2.1.191",
  "gitBranch": "main"
}
```

## 三 spike 实验 session jsonl 考证结果

| 实验 | session jsonl 路径 | usage 字段 | 模型 | 备注 |
|------|---------------------|------------|------|------|
| T5-A 715202a3 | `.map/claude-runtime-home-host/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/<uuid>.jsonl` | ✅ 完整（input/cache_creation/cache_read/output） | claude-sonnet-4-6 | 完整闭环 |
| T2 b3ec2e4d | 同上（按 persona 分目录） | ✅ 完整 | claude-sonnet-4-6 | 短链路 |
| T3 7aeabc2e | 同上 | ✅ 完整 | claude-sonnet-4-6 | 中等链路 |

## 字段映射（Layer 2 mapper dict 草案）

```python
VERSION_FIELD_MAP = {
    "2.1.191": {
        "input_tokens": "input_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
        "cache_read_input_tokens": "cache_read_input_tokens",
        "output_tokens": "output_tokens",
        "model": "model",
        "timestamp": "timestamp",
        "session_id": "sessionId",
    },
}
```

## 损坏行样本（host 实际查证 2026-08-31）

- sessionId 为空字符串的行（极少数，< 0.01%）：聚合时 warn 不崩
- usage 字段缺失的行（type=user 的消息正常无 usage）：按 type=assistant 过滤

## 缺字段模式

- type=user 的消息：normal 无 usage（不算缺）
- type=assistant 但 usage 缺失：极少数，warn + unknown bucket
- type=other（如 queue-operation）：完全不含 usage，按 event_type 过滤跳过

## 修正后的依赖与数据源

```yaml
# dependencies 修正
dependencies:
  - "实际数据源：.map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/*.jsonl"
  - "session jsonl 字段：type/message.usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}/model/sessionId/timestamp"
  - "原 plan 假设的 .map/runtime-waker-sessions/*.jsonl 不含 usage 字段，需替换"
```

## 下一步

1. **revise_plan** 提交给 reviewer：将数据源从 `.map/runtime-waker-sessions/*.jsonl` 改为 `.map/claude-runtime-home-*/.claude/projects/.../*.jsonl`
2. I1 Layer 1 采集器：扫描 claude-runtime 路径下的 `<session_id>.jsonl`，提取 `type=assistant` 行的 `usage` 块
3. I2 Layer 2 映射器：按 `version + field_name` dict 映射
4. I3 归属映射：session 文件路径含 persona（按 persona 目录）；sessionId 与 waker 启动会话可关联
5. I4 命令入口：`map experiment show --cost` + `map waker costs --by-persona/--by-experiment`
6. I5-I7 同 plan

## spike 教训

**为什么 T5-B I0 spike 重要**：原 plan 凭直觉假设数据源是 runtime-waker-sessions，没做实证。I0 spike 直接证伪：实际 usage 在 claude-runtime-home-*/.claude/projects/ 路径。如不做 spike，I1-I6 编码后才发现会全返工。

**Plan §验收 A10（视图类实验硬约束）仍适用**：本实验是数据类而非视图类，但同帧一致性测试思路可借鉴——采集结果与 raw jsonl 累加对账（误差 < 1%）已写进 A8 (h) case。
