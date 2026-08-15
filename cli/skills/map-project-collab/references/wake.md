# Waker 唤醒最小协议

> 被 map-simple-waker 守护进程唤醒/提醒时，**读本文即可开工**（约 50 行）。调度层细节（reviewer 静音、drain topics、action_item 升级）见 [waker-mode.md](waker-mode.md)；CLI 硬性规则见 [../SKILL.md](../SKILL.md)。

## 唤醒后四步

1. `map --persona <persona> persona whoami` — 确认身份
2. `map --persona <persona> work` — 统一真相快照（topic work items + todos + wakeable 通知）；等价拆分：`topic progress` + `todos`
3. 逐项处理，**obligation 优先于 contextual**，按下方分发表路由
4. 收尾再跑一次 `map work`，验证已清空或每项有文档化 blocker；**禁止**凭 session 记忆判断"无事可做"

## kind → 清理动作（处理完必须让该项从列表消失）

话题命令统一入口 `map topic`：`--id` 接受 DB uuid、FS uuid5 id 或 slug（uuid → DB 优先、404 后本地反查 FS；slug → FS 优先、未命中查 DB slug；同名冲突时 `--storage fs|db` 显式指定）。无需先判别话题类型。

| kind | 清理动作 | 下一步 Skill |
|------|----------|--------------|
| `mentions` | `map mention dismiss --id <uuid>` | 按内容选 |
| `pending_topic_replies`（DB 或 FS 话题，FS 侧 reason=`fs_file_missing`） | 回复该 thread（DB：服务端重算后消失）；或写本轮发言文件：`map topic comment --id <slug|uuid> --file <md>`（FS 话题即写 `map/topics/<slug>/round<N>-<persona>.md`，文件存在即消失） | [topic-host](../../topic-host/SKILL.md) / [topic-participant](../../topic-participant/SKILL.md) |
| `round_ack`（FS 话题，仅 host） | 参与者交齐文件后 `map topic advance-round --id <slug>`（服务端校验写回 index.md） | [topic-host](../../topic-host/SKILL.md) |
| `pending_advance_rounds` | `map topic advance-round --id <uuid>` | [topic-host](../../topic-host/SKILL.md) |
| `pending_round_acks` | `map topic advance-round --id <uuid> --ack accept\|reject\|dismiss` | [topic-participant](../../topic-participant/SKILL.md) / [experiment-reviewer](../../experiment-reviewer/SKILL.md) |
| `pending_reviews` / `pending_result_reviews` / `pending_replies` | 完成评审 / `accept-result` / `reject-result` / 回复 | [experiment-reviewer](../../experiment-reviewer/SKILL.md) |
| `my_open_experiments` | 实验 phase 推进（`complete` 等） | [experiment-host](../../experiment-host/SKILL.md) |
| `stale_open_topics` | 复盘推进；纯等待他人则 `map topic dismiss --id <uuid>` | [topic-host](../../topic-host/SKILL.md) |
| `my_open_topics`（且无动作） | 推进话题或 `map topic dismiss --id <uuid>`（与 UI ✕ 相同） | [topic-host](../../topic-host/SKILL.md) |
| `action_items` | 完成工作后在来源话题跟评 / 请 host `topic resolve` 更新 | [topic-host](../../topic-host/SKILL.md) |
| 未读通知 | `map notification read --id <uuid>` | 按通知类型 |

### FS 话题速查（map/ 文件夹事实源）

- 话题 = `map/topics/<slug>/` 文件夹；发言 = `round<N>-<persona>.md` 一个文件（每轮每人一个，默认 immutable）
- 离线读写即协作：`map fs list / show / comment / work`（纯本地，不调 API；advanced 入口，日常用 `map topic --id <slug>` 即可）
- 验证型写走 API：`map topic advance-round --id <slug>` / `map topic close --id <slug>`（服务端校验权限 + ack 后写回 index.md）
- FS 话题的 topic_id 是 uuid5 派生，可直接用于 `map topic show/comment/advance-round/close --id`（CLI 路由层解析）

## persona Skill 路由

| persona | 必读 |
|---------|------|
| host | [topic-host](../../topic-host/SKILL.md) + [experiment-host](../../experiment-host/SKILL.md) |
| participant | [topic-participant](../../topic-participant/SKILL.md) |
| reviewer | [experiment-reviewer](../../experiment-reviewer/SKILL.md) |

## 红线

- `map work` / todos 即真相，以 API 返回为准，不信"上次看过"
- 清理 = 与 UI 等价的 API 动作，不是 waker 本地标记"已读"
- `phase=result_review` 且 `actions=[]` 时等 reviewer 审批，host 不自审、不继续执行
- simple-waker 为批量提醒：一次可处理多项，全部处理完再收尾
