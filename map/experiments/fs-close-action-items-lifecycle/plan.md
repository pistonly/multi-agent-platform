---
title: "FS close_note 的 action_items 解析入跟踪:断开两年半的执行项闭环"
acceptance:
  - "A1 FS close 建行:close_note 含结构化 action_items 段(`## action_items` + `- owner: <agent_name>`/`title:`/`status: open|done`,按 participant round1 约定化格式)→ close 流程在 DB 建 TopicActionItem(复用 topic_resolve_service 创建点语义:owner/status=open/first_open_at/audit)并回显「已登记 N 条 action item」"
  - "A2 门禁辅助:close 校验时若 close_note action_items 段存在 `status: open` 行 → 默认 409(round_ack_pending 同形态的 actionable error)引导先 done / 显式转跟踪;合法路径(status 全 done 或显式声明转跟踪)不受阻(方案 1 与 2 边界按 participant「close 时点就该完成」拆法落地)"
  - "A3 存量回填:一次性回填已 close 的 FS 话题中 close_note 带 action_items 的存量项(含 merge-github-main-into-local 活标本)→ `map topic show` 能见、`map action list` 可查,执行后 complete 清除"
  - "A4 todos 联动流通:建行的 open 项进 `map work` todos 的 action_items 桶(owner 正确、status open),WAKE/STALE 升级时间线可 mock now 走通(复用既有机制,不新增 waker 逻辑)"
  - "A5 CLI 反馈出口:`map fs close`(及 `map topic close` fs 路由)完成后提示登记条数;解析失败/owner 未知给出 actionable error 而非静默丢"
  - "A6 纯文本不留执行项:close_note 无结构化 action_items 段时不建行也不 409(结论性 close 不受扰);close_note 纯文本但有 `- item` 无 owner 时按格式约定报 error 引导规范书写"
  - "测试面:close 解析/建行/门禁/回填的新增单测全绿;已有 fs_source / topic close 测试无回归;`ruff check` 通过"
evidence_keys:
  - "pytest_summary:FS close 解析建行/门禁/格式校验新增单测全绿"
  - "实测输出:merge-github-main-into-local 回填后 `map action list` 列出该项 + complete 走通(A3/A4 活标本)"
  - "grep 核证:建行发生在已有 TopicActionItem 创建通路(非新表);close 校验 409 文案含分段原因"
dependencies:
  - "主话题 fs-close-action-items-lifecycle(281de0d5-ea93-536f-89ef-93e0870b5c26)Round 2 Summary 定稿:方案 2(跟踪)为主 + 方案 1(门禁)辅助 + close_note action_items 格式约定化(owner/title/status 结构化段,participant round1 细化 3);存量 merge 补执行在本话题记录不 reopen"
  - "活标本:merge-github-main-into-local(103d6482)close_note 的 action_items「host 执行 merge...push github 前需用户确认」为存量回填实测对象;merge 本身执行与 push github 仍保持 close_note 门禁语义(用户显式确认)"
  - "前置:4b1192cc advance-round ack 合规字段已落地(本次 close 校验的 409 形态复用其 actionable error 渲染);FS close 走 validated_write_flow,commit 端有 DB session 可建行(server/api/fs.py _COMMIT_EVENT['close'])"
  - "观察项:fs-close-action-items-lifecycle round1-host.md 存在 posted_at='$ts' 未解析占位符(发起帖脏 fixture,不在本实验修,另判)"
---

# FS close_note 的 action_items 解析入跟踪：断开两年半的执行项闭环

## 背景

话题 `merge-github-main-into-local`(103d6482)于 08-23 close，close_note 写明 action_items「host 执行 merge……push github 前需用户确认」，但 **merge 至今未执行**（main vs github/main = ahead 30 / behind 19）。用户实证指出：话题没执行操作就关闭不合理。

根因是机制断链（非执行者过失）：

1. **DB 时代 action_items 全链机制健在**：`topic_resolve_service.py:175` 从 resolve payload 建 TopicActionItem（owner/status/due/first_open_at/audit）→ `todo_service` action_items 桶 → waker T+24h/72h 升级唤醒（list_stale_open_action_items / mark_wake_sent_action_item）→ complete 清除。
2. **FS 路径（v0.13 起）绕开了这一切**：`map topic close` / `fs close` 只把 close_note 当纯文本写回 index.md，无解析器、不入 TopicActionItem → todos 桶空。
3. **close 后义务真空**：stale_open_topics nudge 只对 open 话题；closed 话题不产生任何 waker 义务 → close_note 执行项永远无人推动。

## 定稿决议（Round 2 Summary）

| # | 决议 | 说明 |
|---|------|------|
| D1 | 方案 2（跟踪）为主：FS close 解析 close_note 结构化 action_items 段 → 复用 DB 建行机制持续推动 | 不自建 FS 侧任务跟踪；DB 侧 owner/status/升级/complete 全套机制直接复用 |
| D2 | 方案 1（门禁）为辅助防线：close 时存在 `status: open` 行 → 409 引导先 done / 显式转跟踪 | 防“明显没做就关”的低级蒸发；不替代跟踪 |
| D3 | close_note action_items 格式约定化（participant round1 细化）：`## action_items` + `- owner: <agent_name>` / `title:` / `status: open\|done` | owner 可解析回 agent、status 显式决定“什么算 pending”，消除解析器脆弱与判据模糊 |
| D4 | 存量处置：不 reopen；merge 存量回填为可查 action item，执行结果记本话题（host 执行 merge 仍须用户对 push github 显式确认） | reopen 是重型操作；断链→补执行用活标本走通 A3/A4 |

## 调研事实（已核证）

| 事实 | 位置 |
|------|------|
| 唯一 TopicActionItem 创建点（owner 校验 / first_open_at / audit 同事务） | `server/services/topic_resolve_service.py:175`（resolve_service 路径） |
| FS close 是纯 FS 写回：validate 返回 fields→ CLI 本地 write-back → commit | `validate_fs_close` `server/services/fs_source_service.py:1492`；`validated_write_flow` `cli/commands/fs.py:433` |
| commit 端点有 DB session + applied_fields（可在此桥接建行） | `server/api/fs.py:410-459`（`fs_write_commit`，action `close` 在 `_COMMIT_EVENT`） |
| CLI action 全套出口在（list/complete/deliver/cancel/link/mark-wake-sent/mark-stale） | `map action --help` |
| FS close 会写 close_note 到 index.md（纯文本） | `validate_fs_close` fields `{"status":"closed", "close_note": ...}` |
| 409 actionable error 渲染范例（4b1192cc 落地） | `cli/commands/fs.py:_render_ack_error` |

## 实施顺序（建议，评审可调）

1. **I1 close_note action_items 解析**（D3 格式约定 + A6）：parser/校验层把 close_note 的结构化段拆成 `(owner, title, status)` 列表；无段→0 项；格式错漏（无 owner / 裸 item）→ 带原因 error
2. **I2 建行桥接**（D1 + A1）：`fs_write_commit` 的 `close` 分支拿 applied_fields 里的 close_note → 解析 action_items → 复用 TopicActionItem 创建通路建行（owner 解析回 agent、status=open、first_open_at、audit）；回显登记条数给 CLI
3. **I3 门禁辅助**（D2 + A2）：`validate_fs_close` 检查 close_note 段含 `status: open` 行 → 409（可带 owner 未知/寄生原因），不阻断全部 done 或显式转跟踪的合法 close
4. **I4 存量回填**（D3 存量 + A3）：一次性回填已 close FS 话题 close_note 带 action_items 的项（merge-github-main-into-local 为活标本）
5. **I5 验证收尾（全部）**：A1-A6 单测 + 活标本实测 + todos 流通（A4 mock now）+ ruff check + 回归

## 风险与边界

- FS close 现在不碰 DB；建行桥接只发生在 `fs_write_commit` 的 `close` 分支（有 session），remote 模式与同机路径行为一致——需在 plan review 确认 remote（pure token）模式下 applied_fields 是否可靠携带 close_note（4b1192cc 已确认 fields 由 token 承载）
- 旧客户端 / 无 close_note 话题不建行（A6），不追溯手写 close_note（纯结论性 close 不受扰）
- 门禁 409 可能误伤存量习惯（close_note 写 `- [ ]` 任务列表）——格式约定化后引导改结构化段，不静默吞
- `posted_at: '$ts'` 脏 fixture（本话题 round1-host.md）不在本实验修，作为观察项另判
- 工作树存在其他并行未提交改动，本实验只 stage 自身文件，不混提交

## 存量 merge 处置（独立于本实验代码交付）

- merge-github-main-into-local 的 merge 执行属外部操作：host 先补执行本地 merge（可回退、可验证），**push github 前必须用户显式确认**（保持 close_note 门禁语义）
- 本实验只把该项回填成可跟踪 action item（A3），执行本身由 host 在 topic comment 记录进度
