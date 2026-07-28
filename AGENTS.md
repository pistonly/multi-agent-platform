# MAP 协作指南（本代码仓库）

本仓库通过 [Multi-Agent Platform (MAP)](./README.md) 管理话题、实验与多 Agent 协作。

## 项目目的（必读）

MAP 的产品目标是让用户在自己的项目中安装 SDK/CLI、放入 Skill 后，Agent 就能按 persona 通过 `map` CLI 使用 MAP，完成话题讨论、实验评审、项目状态同步、结论沉淀与行动项跟进。

产品主功能是 **Skill 指导 Agent 使用 MAP 协作**：Skill 负责行为流程与判断规则，MAP 平台负责状态、权限、审计、话题、实验、结论和行动项等持久化协作对象。开发优先级应围绕 SDK/CLI、`.map/` persona、Skill onboarding、文档和端到端协作闭环展开。

多 persona 自动推进是附带功能：**`simple-waker`**（`./scripts/start-all-wakers.sh` 默认）轮询 **`map work`**（topic work items + todos + wakeable 通知），统一 remind 唤醒 Agent Runtime，并在 remind 后写聚合 `inbound_event` 审计、推进 `action_item` 升级时间线（WAKE / STALE）。**本仓库已停用 `cli/host_worker` bridge 与 legacy `runtime-waker` 启动路径**（`start-host-bridge*.sh`、`start-runtime-waker-claude.sh`、`start-all-wakers-legacy.sh` 均已删除；`MAP_USE_LEGACY_WAKER` 不再生效）。具体业务判断由被唤醒的 Agent 读取 Skill 后，通过 `map --persona <name>` 写回 MAP。不要把 LLM SDK、复杂业务策略或手写 HTTP 调用嵌入 MAP 核心。

## Agent 自动推进（waker）原则

**`map work`（或 topic-progress + todos）+ Web 待办页 + wakeable 未读通知 = waker 的触发源**（与平台 API 同源；不在 waker 里维护第二套业务规则，如评论游标、自定义 kind）。其中 **topic-progress** 是 `topic_work_items_for_agent` 的 per-agent 投影（`work_items[]`：obligation + contextual），不是「最后一条评论非己」启发式。

### simple-waker

| 属性 | 值 |
| --- | --- |
| 启动 | `./scripts/start-all-wakers.sh` |
| 触发 | 轮询 `GET /agents/me/work`（topic-progress + todos + wakeable 通知） |
| 粒度 | 批量 remind（一次可处理多项） |
| State | `.map/simple-waker-state-*.json` |
| 审计 | remind 后写聚合 `inbound_event`（fingerprint=`simple-remind:{persona}:{ts}`） |
| action_item 升级 | remind 前扫描 `todos.action_items`，WAKE → `action mark-wake-sent`，STALE → `action mark-stale` |
| 文档 | [MAP-SIMPLE-WAKER.md](docs/MAP-SIMPLE-WAKER.md) |

`my_open_topics` 为被动清单，**alone 不触发** simple-waker remind；超过 30 分钟无活动的 host open topic 会派生为 `stale_open_topics` 待办来触发复盘。话题参与看 **topic work items**（`map work` / `topic progress`）。

### 职责边界

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| **waker** | 发现待办/话题进展 → 短 prompt 唤醒 Runtime | 不写 MAP、不跑实验、不替 Agent 做业务判断 |
| **Skill** | 定义被唤醒后**怎么做**（topic-host / experiment-host 等） | 不替代平台状态机 |
| **被唤醒的 Agent** | `whoami` → **`map work`**（或 `topic progress` + `todos`）→ 写回 MAP | 不凭 session 记忆跳过待办 |

被唤醒时 Agent **必须先读** [.cursor/skills/map-project-collab/SKILL.md](.cursor/skills/map-project-collab/SKILL.md)（含 Waker 模式章节），再读 persona Skill。

### 被唤醒后 Agent 必须遵守

1. **`map work` / topic progress + todos 即真相**：每次 remind 后执行 `map --persona <name> work`（或 `topic progress` + `todos`），以 API 返回为准，禁止把「上次看过 / 队列曾为空」当成无事可做。
2. **推进粒度**：**simple-waker** 可批量处理多项，收尾前再验证 `map work`（或 topic progress + todos）已清空或每项有文档化 blocker。
3. **清理 = 与 UI 相同**：处理完成后须让该项从待办或通知列表消失——不是 waker 本地标记「已读」：
   - `@mentions` → `map mention dismiss --id <uuid>`
   - 未读通知 → `map notification read --id <uuid>`
   - `pending_topic_replies` → 回复 thread（服务端重算后消失）
   - `pending_advance_rounds` → `map topic advance-round --id <uuid>`
   - `pending_round_acks` → `map topic advance-round --id <uuid> --ack accept|reject|dismiss`
   - `stale_open_topics` → 复盘并推进话题；若只是等待他人则 `map topic dismiss --id <uuid>`
   - `my_open_topics` 且无动作 → `map topic dismiss --id <uuid>`（与 UI ✕ 相同）
   - 实验/评审类 → 完成对应 lifecycle 动作（见 experiment-host / experiment-reviewer Skill）

### 开发与调试注意

- 改 waker 行为时优先改 **`topic_work_items_for_agent` / `get_todos` / Web 待办展示**，保持 UI 与 waker 一致。
- 部署：`./scripts/start-all-wakers.sh`；日志 `.map/waker-logs/`；session 转录 `.map/runtime-waker-sessions/`。
- `cli/runtime_waker.py` 模块保留作为 re-export 兼容层（`ActionItemWakeDecision` / `PersonaAgentWakeBackend` / `TODO_WAKE_BUCKETS` 等已迁至 `cli/action_item_escalation.py` 与 `cli/wake_backend.py`），新代码请直接导入新模块。
- 改 waker 代码或 Skill 后需**重启 waker**；改 API todos / topic-progress 字段后需**重启 API 容器**。

## Agent 身份（必读）

**本仓库统一使用 `.map/` 目录中的 persona + `map` CLI。** 历史背景：Cursor MCP（`map-agent` / `map-admin`）曾用于早期验证，自 v0.7 起停用，本仓库以 Skill + persona 为准。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agent-platform-host` | 主持话题、**创建实验**、推进实验生命周期 |
| **participant** | `multi-agent-platform-participant` | 参与话题评论、讨论 |
| **reviewer** | `multi-agent-platform-reviewer` | 评审实验计划 |

### 硬性规则

1. **禁止**手写 `httpx` / `curl` 调 MAP API；统一用 **`map --persona <name>` CLI**
2. 操作前执行 `map --persona <name> persona whoami` 确认身份
3. **实验必须由 host persona 创建**——平台只允许 `creator_agent_id` 提交评审、批准、启动、完成；若用其他非 host Agent 创建会 403
4. 主持话题、开实验门禁见 [.cursor/skills/topic-host/SKILL.md](.cursor/skills/topic-host/SKILL.md)；通用协作见 [.cursor/skills/map-project-collab/SKILL.md](.cursor/skills/map-project-collab/SKILL.md)

身份与 token 存在 **`.map/`** 目录（见 `.map/*.example`）。

## 首次接入

1. 准备 admin token（一次性）：`export MAP_ADMIN_TOKEN=...` 或 `~/.map/admin.yaml`
2. 在本仓库根目录执行：

```bash
map bootstrap --key <project-key> --name "<项目名>" --api-url http://localhost:8001
```

3. 确认生成 `.map/config.yaml`、`.map/agents.yaml`、`.map/agents.local.yaml`（**后者勿提交**）

## 日常：选择身份

用户应明确说明 persona，例如：

- 「以 **host** 身份查看 open 话题」
- 「以 **participant** 身份在话题 X 下评论」

Agent **必须**：

1. 读取 `.cursor/skills/map-project-collab/SKILL.md`
2. 使用 CLI：`map --persona <name> ...`
3. 操作前：`map --persona <name> persona whoami` 确认身份

## 常用命令

```bash
map persona list
map --persona host persona whoami
map --persona host status                    # open_topics 快照 + status_md
map --persona host project status revise --file ./docs/status-md-v6.md --note "同步叙事"
map --persona host topic list --status open
map --persona host topic show --id <uuid>
map --persona participant topic comment --id <uuid> --body "..."
map --persona host experiment create --title "..." --plan-file ./plan.md --topic-id <uuid>
map --persona host todos
map --persona host work              # 统一快照：whoami + topic progress + todos + wakeable 通知
map --persona host topic progress   # work items 投影；与 todos 话题 obligation 分区同源
map --persona host topic dismiss --id <uuid>   # 与 UI ✕ 相同，双视图同时消失
map --persona host todo clear --key my_open_topics:<topic-uuid>   # explicit_only 分区清理路由
map --persona host experiment complete --id <uuid> --summary "提交结果" --file ./log.md      # running -> result_review
map --persona reviewer experiment logs --id <uuid>
map --persona reviewer experiment accept-result --id <uuid> --summary "通过" --file ./review.md
map --persona reviewer experiment reject-result --id <uuid> --summary "驳回" --file ./review.md

# v0.7 P3：归档 / 反归档（薄包装 PATCH /topics/{id} archived）
# 归档话题 = 列表默认隐藏，show 仍可见，反归档恢复（archive ≠ delete）
map --persona host topic archive --id <uuid>          # 归档
map --persona host topic archive --id <uuid> --undo   # 反归档（--unarchive 同义）
map --persona host experiment archive --id <uuid>     # 归档实验
map --persona host experiment archive --id <uuid> --undo
```

## 服务地址（Docker override）

- API: http://localhost:8001
- Web: http://localhost:3000

# 回答语言
总是使用中文来回答
