# Waker 唤醒最小协议

> 被 map-simple-waker 守护进程唤醒/提醒时，**读本文即可开工**（约 50 行）。调度层细节（reviewer 静音、drain topics、action_item 升级）见 [waker-mode.md](waker-mode.md)；CLI 硬性规则见 [../SKILL.md](../SKILL.md)。

## 唤醒后四步

1. `map --persona <persona> persona whoami` — 确认身份
2. `map --persona <persona> work` — 统一真相快照（topic work items + todos + wakeable 通知）；等价拆分：`topic progress` + `todos`
3. 逐项处理，**obligation 优先于 contextual**，按下方分发表路由
4. 收尾再跑一次 `map work`，验证已清空或每项有文档化 blocker；**禁止**凭 session 记忆判断"无事可做"

## kind → 清理动作（处理完必须让该项从列表消失）

话题命令统一入口 `map topic`：`--id` 接受 DB uuid、FS uuid5 id 或 slug（uuid → DB 优先、404 后本地反查 FS；slug → FS 优先、未命中查 DB slug；同名冲突时 `--storage fs|db` 显式指定）。无需先判别话题类型。

<!-- BEGIN:kind-dispatch (generated: map work --kinds --kinds-format md)  -->
| kind | 清理动作 | 下一步 Skill | 说明 | required_role | obligation_whitelist_exempt |
|------|----------|--------------|------|---------------|----------------------------|
| `mentions` | map mention dismiss --id <uuid> | map-project-collab | mention 功能保留；实验评论仍产生，话题域来源已随 DB 写路径退役枯竭 | all | False |
| `pending_topic_replies` | 写本轮发言文件：map topic comment --id <slug> --file <md>（即写 map/topics/<slug>/round<N>-<persona>.md，文件存在即消失）；存量 DB 话题只读，需 host 先 topic migrate | topic-host | FS 话题 reason=fs_file_missing；participant 视角见 topic-participant | participant | False |
| `round_ack` | 参与者交齐文件后 map topic advance-round --id <slug>（服务端校验写回 index.md；等价 topic advance-round --topic <slug>） | topic-host | 仅 host；FS 话题 | host | False |
| `pending_advance_rounds` | 读路径保留；推进已退役——host 先 topic migrate --id <uuid>迁 FS 后用 topic advance-round --id <slug> | topic-host | 存量 DB 话题 | host | False |
| `pending_round_acks` | FS 话题=写本轮自己的发言文件（发言文件即表态）；存量 DB 话题=只读（迁移后表态），DB --ack 命令已退役 | topic-participant | reviewer 视角见 experiment-reviewer | participant | False |
| `pending_reviews` | 完成评审（experiment review add） | experiment-reviewer |  | reviewer | True |
| `pending_result_reviews` | accept-result / reject-result | experiment-reviewer |  | reviewer | True |
| `pending_replies` | 回复 | experiment-reviewer |  | reviewer | True |
| `my_open_experiments` | 实验 phase 推进（complete 等） | experiment-host |  | host | False |
| `executor_assignments` | 被委派 participant 通过 lock acquire → 改仓库 → 写 experiment log → complete → release 推进 | experiment-executor | running 实验且 executor_agent_id == me.id；查询排除 creator_agent_id == executor_agent_id 的自执行（避免与 my_open_experiments 重复）。仅 participant 角色；host 已委派即 对其 informational_only。 | participant | False |
| `stale_open_topics` | 复盘推进；FS 话题（仅 creator/host 可见）久未推进→ map topic close --id <slug> --note 落结论即清理（dismiss 对 FS 是 no-op）；存量 DB 话题纯等待他人则 map topic dismiss --id <uuid> | topic-host |  | host | False |
| `my_open_topics` | 推进话题或 map topic dismiss --id <uuid>（与 UI ✕ 相同） | topic-host | 且无动作时 | host | False |
| `action_items` | 完成: map topic action-item complete --topic <slug> --id <n> --evidence "<commit/pytest/路径>"；放弃: map topic action-item cancel --topic <slug> --id <n> --reason "..."。清零后话题才可 close（closed = 零尾款） | topic-host | FS 话题，kind 同构于 stale nudge，来源 action-items.yaml；participant 亦可为 owner | host | False |
| `unread_change` | 读最新发言并接棒：map topic show --id <slug> 查看后，写本轮发言文件 map topic comment --id <slug> --file <md>（host 收尾加 --round-summary 写独立 Summary 文件；写完自己成为最新发言者即消失） | map-project-collab | contextual 交接信号：对方是最新发言者时出现，我自己发言后消失；仅白名单（creator ∪ declared ∪ speakers）可见，reviewer 等旁观者不可见；simple-waker 签名唤醒的交接信号源，不再依赖 stale_open_topics 心跳 | all | False |
<!-- END:kind-dispatch -->

> 未读通知：`map notification read --id <uuid>`（按通知类型处理）。

### FS 话题速查（map/ 文件夹事实源）

- 话题 = `map/topics/<slug>/` 文件夹；发言 = `round<N>-<persona>.md` 一个文件（每轮每人一个，默认 immutable）
- 离线读写即协作：`map topic list / show / comment / work`（纯本地，不调 API；advanced 入口，日常用 `map topic`）
- 验证型写走 API：`map topic advance-round --id <slug>` / `map topic close --id <slug>`（服务端校验权限 + ack 后写回 index.md）
- FS 话题的 topic_id 是 uuid5 派生，可直接用于 `map topic show/comment/advance-round/close --id`（CLI 路由层解析）
- **v0.13 M58 起 DB 话题写路径退役**：对 DB uuid（或 `--storage db`）跑写命令返回引导性错误——`topic create / list / show` 已兼容 FS；存量话题继续讨论先 `topic migrate`

## persona Skill 路由

| persona | 必读 |
|---------|------|
| host | [topic-host](../../topic-host/SKILL.md) + [experiment-host](../../experiment-host/SKILL.md) |
| participant | [topic-participant](../../topic-participant/SKILL.md) + [experiment-executor](../../experiment-executor/SKILL.md)（host 委派的 direct 实验） |
| reviewer | [experiment-reviewer](../../experiment-reviewer/SKILL.md) |

## 写入红线（runtime 中立）

**红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许。

**视为事故触发条件**：发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）→ 停止当前话题状态变更 → 报告 → 等 host/supervisor 决定。

**历史踩坑**：participant 不要绕过 `map topic comment` 手写 round 文件——手写路径即使在文件层可见，server 端 ack 校验 + audit 留痕都缺失，会被 verify-audit D001/D003 检出。

## 红线

- `map work` / todos 即真相，以 API 返回为准，不信"上次看过"
- 清理 = 与 UI 等价的 API 动作，不是 waker 本地标记"已读"
- `phase=result_review` 且 `actions=[]` 时等 reviewer 审批，host 不自审、不继续执行
- simple-waker 为批量提醒：一次可处理多项，全部处理完再收尾
