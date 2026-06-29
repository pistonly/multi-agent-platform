# 目标（v2 — 按评审修订）

P0 交付后，补齐主持工作流 **P1**：**话题评论广播 + 主持定向通知** + **实验 topic_id 软校验**。

**源话题**：`e5553c84-25ef-4b81-9a86-e75746077bee`

---

# Part A — 话题评论：广播保留 + 主持额外定向

## A1. 实现

- 保留 `enqueue_from_event` 项目广播（排除 actor 与主持，避免主持重复）
- 主持另收 `enqueue_for_agents`：`【主持】话题新评论待回复`（`host_directed: true`）
- `emit(notify=False)` + `notify_topic_comment_created()`

## A2. 验收

- [x] 非主持 participant 仍收广播「话题新评论」
- [x] 主持收定向通知，无重复
- [x] 主持自评论无自通知
- [x] @mention 逻辑不变

---

# Part B — topic_id 软校验

## B1. API

`ExperimentSummaryRead.warnings: list[str]`；有 open 话题且 `topic_id` 为空 → `["no_topic_id"]`（201 不阻断）

## B2. CLI

`map experiment create --topic-id <uuid>`；warnings 打印 stderr

## B3. 验收

- [x] 无 topic + 有 open 话题 → warnings
- [x] 显式 topic_id → 无 warnings
- [x] strict 门禁不变

---

# 非目标

议题2（@主持 direct reply 例外）
