---
title: "fs-participant-whitelist：FS 话题参与人白名单"
acceptance:
  - "reviewer persona 对未声明参与的 open FS 话题零 FS 待办（derive_work 白名单过滤）"
  - "write_topic_index(participants=[...]) 将声明列表写入 index.md front-matter"
  - "write_round_comment 新作者自动并入 index.md participants（发言即参与）"
  - "fs topic-create --participants a,b 选项可用"
  - "存量话题（无 participants front-matter）行为与改造前完全一致（发言者 ∪ creator）"
  - "advance-round 的 ack missing 计算仍覆盖全量 participants（语义不收缩）"
  - "pytest tests/test_fs_source.py 全绿，fast gate 无回归"
dependencies:
  - "map/ 文件夹事实源链路（sdk/python/map_fs + server/services/fs_source_service，已上线）"
evidence_keys:
  - "tests/test_fs_source.py 新增白名单用例（白名单外零待办 / 声明生效 / 自动并入）"
  - "map/experiments/fs-participant-whitelist/log.md 实施记录"
---

# 实验计划：fs-participant-whitelist — FS 话题参与人白名单

## 背景与问题

`derive_work()` 对**任意 persona** 生成 `pending_topic_reply`（本轮尚无我的文件即待办），
而 `fs_topic_progress_for_agent()` 对项目内所有 agent 无差别投影——导致 reviewer 会收到
每一个 open FS 话题的待办噪音（slimming-e2e 话题实证：reviewer 从未参与却持续挂待办）。

话题 fs-refactor-review round1 中 participant 确认该缺陷（争议点 3），round2 决策：
**参与人白名单**方案，单开本实验实施。

## 目标

1. 只有「话题参与者」才会从 FS 话题收到 `pending_topic_reply` 待办
2. 参与者身份可显式声明（index.md front-matter `participants:`），也可由发言事实自动并入
3. 向后兼容：无 front-matter participants 的存量话题行为不变（发言者 ∪ creator）

## 方案设计

### 语义：谁算参与者

```
participants = declared（front-matter，topic-create 时声明）
             ∪ speakers（所有发言过的 persona，事实参与）
             ∪ creator（默认参与）
```

- `pending_topic_reply`：仅 persona ∈ participants 时生成
- `round_ack_pending`（host 视角）：missing 计算仍用全量 participants（语义不变）
- 白名单外 persona（如 reviewer）：不产生任何 FS 待办；需要其参与时由 host 将其加入
  front-matter（或在正文中协商后由其主动发言——发言即自动并入）

### 改动清单

| 层 | 文件 | 改动 |
|----|------|------|
| SDK | `sdk/python/map_fs/parser.py` | `FsTopic.declared_participants` 字段；`parse_topic_dir` 读 front-matter；`participants` property 合并三源；`write_topic_index` 加 `participants` 参数；`write_round_comment` 新作者自动并入 index.md；`derive_work` 白名单过滤 |
| CLI | `cli/commands/fs.py` | `topic-create --participants a,b` 选项 |
| 测试 | `tests/test_fs_source.py` | 白名单内外待办差异、自动并入、向后兼容 |

### 实施步骤

1. parser：`declared_participants` 解析 + `participants` property 合并（兼容旧话题）
2. parser：`write_topic_index(participants=...)` 写 front-matter
3. parser：`write_round_comment` 后调用 `update_topic_index` 把新作者并入（index 缺失时容错跳过）
4. parser：`derive_work` 的 `pending_topic_reply` 分支加白名单判断
5. CLI：`fs topic-create --participants`（逗号分隔）
6. 测试：新增 3 个用例 + 既有用例回归

### 验证方法

- `pytest tests/test_fs_source.py -q` 全绿（新增：白名单外 persona 零待办；声明 participants
  后非成员无待办、成员有待办；新发言人自动并入后待办出现）
- 快速门禁回归：`pytest -m "fast"`（或 conftest fast gate 白名单内模块）

### 风险与边界

- **并发写 index.md**：comment 自动并入会写 index.md，与 advance-round 写回存在竞争窗口；
  当前单 host + 每轮每人一文件的粒度下冲突面极小，维持「观察项」定级（话题决策 #6）
- **手工编辑 front-matter 容错**：participants 非列表 / 含空串时按忽略处理（容错优先，
  与本模块「事实源是手写文件」原则一致）
- 不改 service 层：`fs_work_items` / `fs_topic_progress_for_agent` 都走 `derive_work`，
  parser 层过滤即全量生效（waker 路径自动受益）

## 验收标准

1. reviewer persona 对 open FS 话题零 FS 待办（除非被声明/发言过）
2. 存量话题（无 participants front-matter）行为与改造前完全一致
3. 所有测试通过，快照/兼容测试无回归
