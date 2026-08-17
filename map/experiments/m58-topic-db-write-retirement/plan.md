---
title: "M58 DB 话题写路径直接退役（v0.13）"
acceptance:
  - "M58a：topic-host / topic-participant / map-project-collab 三 Skill 的 SKILL.md、references（host-checklist / participant-checklist / experiment-gate-rubric / commands.md / wake.md）无 DB 话题写命令示例残留（grep 核证：`topic comment --body/--file`、`topic advance-round`、`topic resolve`、`topic create --title`、`topic rollback-round`、`topic reopen`、`topic archive` 零残留；只读 list/show/progress、通知投影 dismiss/read/mark-seen、migrate 与 mention 命令保留）；resolve→fs close 的 close_note 承载约定、rollback-round/reopen 的 FS 等价操作约定（回滚=删本轮文件+核对 index.md round/participants 一致性 / 重开=改 index status+说明）写入文档；两个 map-plugin.yaml version 升 0.13.0 且 requires 同步升 0.12（fs 三态路由命令为 v0.12 M56 交付，旧 CLI 无 fs 路由分支，requires 停留 0.11 会让升级 Skill 的旧 CLI 用户撞墙）；cli/skills/ 镜像目录同步"
  - "M58b CLI：topic create / resolve / rollback-round / reopen / archive 五命令的 DB 分支与 comment(--body/--file) / advance-round / close 三命令的 DB 分支（含显式 --storage db）一律返回引导性错误（exit 2，文案指向 fs 等价命令或 topic migrate，不静默成功；CLI 无 topic delete 命令，DELETE 仅 server 端点处置项）；`map topic --help` 顶层含「话题写操作已 FS 单轨化」提示；comment/advance-round/close 的 fs 路由分支行为不回退（M56 三态路由测试全绿）"
  - "M58b server：话题域写端点（POST create / comments / close / reopen / advance-round / rollback-round / resolve 与 DELETE /topics/{id}）返回 410 + 引导 body；PATCH /topics/{id} 保留 archived 用途（topic migrate 收尾归档依赖，title/pinned 引导退役）；dismiss / read / mark-seen / mark-seen 通知投影与全部 GET 只读端点行为不回退；topics / comments / mentions 表与历史数据不删（只读展示可用）"
  - "M58b 测试：受影响测试面完成改造且全量 fast-gate 回归通过，改造按断言语义三类执行（r1 定案：(a) 写端点行为断言→改 410+引导 body 断言；(b) 消费者断言（waker 桶/Web/thread_activity 等）→直插 DB fixtures 保留；(c) 话题域 mention/work-item 产生断言→改 410 断言或移除，实验域产生断言保留为主验证面——禁止用直插造数假绿保留已死链路断言）；17 个 HTTP 写测试文件、test_cli.py DB 写用例、test_topic_routing.py DB 用例改引导断言、test_archive_command.py 改引导断言、test_dry_run_write_commands.py 反射分类同步、test_compat.py 快照同步、test_clear_action_template.py 模板同步、test_docs_consistency.py 与 QUICKSTART.md 同步；test_topics.py 的 close/resolve→action_items 级联语义改断言 service 级联函数；cli/e2e_collab.py 话题流程改走 fs 路径并手跑一轮记录输出摘要"
  - "M58c：mention 机制核证修正结论回写 docs/prd/v0.13.md 风险表（带已决标注）：消费者活跃（waker mentions 桶 / Web 待办与通知页 / CLI map mention / 通知级联 / thread_activity）→ 消费链保留；实验评论来源（comment_service.py:68）仍持续写 Mention 表（实验域按非目标保持 DB）→ 表不退役；话题域来源（topic / topic_comment）随写端点 410 自然枯竭；不做 FS 评论 mention 投影（@唤醒主链路已由 topic.round_advanced 自动唤醒替代）；Skill 文档 mention 表述与实际行为一致"
evidence_keys:
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：7 命令 DB 分支 + comment/advance-round/close 的 DB 分支（10 处分支面）返回引导性错误且文案含 fs 等价指引；topic --help 含单轨化提示；fs comment/advance-round/close 路由行为不回退"
  - "server 实测：DB 写端点返回 410 + 引导 body；GET 只读端点 200 不回退；PATCH archived（migrate 归档）可用"
  - "grep 核证：三 Skill 与 cli/skills 镜像无 DB 写命令残留；map-plugin.yaml version 0.13.0 / requires 0.12 已升版"
  - "M58c 结论已回写 PRD v0.13 风险表（已决标注）"
dependencies:
  - "v0.13 提案评审通过（话题 v013-single-track-closure-review closed，M58 直接退役方案经评审特别确认）；M56 三态路由已交付（comment/advance-round/close 的 fs 路由分支与测试存在）"
  - "waker FS 桥接已就位（fs_topic_progress_for_agent → GET /agents/me/work 统一快照）；DB 存量 4 话题全 closed、comments 表 0 条（写路径零使用，2026-08-16 PRD 核证）"
---

# M58 DB 话题写路径直接退役（v0.13）

## 修订说明（v2，回应 r1 评审）

- unreasonable `28633582`（版本升级不完整 + delete 命令事实错误）：requires 同步升 0.12（依据：fs 三态路由命令为 v0.12 M56 交付，requires 停 0.11 会让升级 Skill 的旧 CLI 用户撞墙）写入 M58a acceptance 与改动范围；CLI 命令清单修正——`@topic_app.command` 全量核证无 `delete` 命令（15 命令 = create/list/show/progress/resolve/advance-round/rollback-round/comment/close/reopen/dismiss/read/mark-seen/archive/migrate + mention 子命令组），退役面改为「7 命令 DB 分支（create/resolve/rollback-round/reopen/archive）+ comment/advance-round/close 三命令 DB 分支」，DELETE 仅作 server 端点处置项。
- unreasonable `a4feba18`（测试改造缺断言语义分类）：M58b 测试 acceptance 与 M58b-3 条目、风险段均已写入三类分类——(a) 写端点行为断言→410 断言；(b) 消费者断言→直插 fixtures 保留；(c) 话题域产生断言→410/移除、实验域产生断言保留为主验证面；明确禁止直插造数假绿保留已死链路断言。
- 非阻塞建议三条全部采纳：rollback FS 等价约定补 index.md round/participants 一致性核对（M58a acceptance）；test_topics.py 级联语义改断言 service 级联函数（M58b 测试 acceptance）；e2e_collab 手跑一轮记录输出摘要（M58b 测试 acceptance + 实现顺序 5）。

## 目标

按 docs/prd/v0.13.md §M58 落地 F2：以 **M58a Skill FS 化为硬前置**，移除 CLI 与 server 的话题域 DB 写路径，完成话题域单轨（FS）闭环；M58c 以核证结论（而非实施）结项。本实验是 v0.13 双轨债务收敛的核心项。

## 调研结论（2026-08-16 代码级核证，两处修正 PRD 前提）

| 事实 | 位置 | 对设计的影响 |
|------|------|--------------|
| CLI 15 命令分布：7 个命令带 DB 写分支（create / resolve / rollback-round / reopen / archive + comment / advance-round / close 的 DB 分支）；3 只读（list / show / progress）；3 通知投影（dismiss / read / mark-seen，M56 已 fs 降级 no-op/引导）；2 存量通道（migrate / migrate-from-docs）；另有 mention 子命令组（:877-914）；**无 delete 命令**（r1 核证） | `cli/commands/topic.py`（create :204、resolve :318、advance-round :338、rollback-round :411、comment :428、close :552、reopen :584、dismiss :601、read :622、mark-seen :643、archive :664、migrate :814） | 退役面 = 7 命令 DB 分支 + comment/advance/close 三命令 DB 分支（10 处分支面）；`--storage db` 显式传参也须引导（不能留暗门） |
| server 写端点：POST create(:32) / comments(:339) / close(:173) / reopen(:218) / advance-round(:242) / rollback-round(:286) / resolve(:314) + PATCH update_topic(:143) + DELETE delete_topic(:163)；fs 侧 advance-round/close 端点在 `server/api/fs.py:89,138`（保留） | `server/api/topics.py` | POST×7 + DELETE → 410；PATCH 有冲突（下行） |
| **PATCH /topics/{id} 双用途冲突**：既是 `topic archive` 的 DB 分支端点，又是保留命令 `topic migrate` 的收尾归档步骤（`c.update_topic(archived=True)`，topic.py:871） | `cli/commands/topic.py:703,871` | PATCH 不能整体 410——保留 archived 用途（或最小保留面），title/pinned 随 archive 退役引导；否则 migrate 归档步骤损坏 |
| DELETE /topics/{id}（软删）不在 PRD 8 命令表内，但属话题域写入口（test_topics.py / test_action_items.py 在用） | `server/api/topics.py:163` | 显式决策：一并 410（无保留依据；话题域写入口全退役原则）——留评审确认 |
| **mention 核证修正 1（来源未枯竭）**：实验评论 `process_experiment_comment_mentions`（comment_service.py:68）仍持续写 Mention 表；实验域按 PRD 非目标「实验/通知仍以 DB 为准」不受本实验影响 | `server/services/mention_service.py:135-201` | 「Mention 表新增来源枯竭」仅对话题域成立（topic :272-299 / topic_comment :204-269 随写端点 410 自然枯竭）；表与消费链**不能退役** |
| **mention 核证修正 2（消费者活跃）**：waker mentions 桶（cli/wake_backend.py:36-49，actionable）、Web TodosPage/NotificationsPage、CLI `map mention *`、通知级联已读、thread_activity 陈旧性过滤、~39 个测试文件均在消费 | 见左列 + todo_service.py:505-532、agent_work_service.py:167-207、thread_activity.py:98-180 | PRD「无消费者 → mention 触发逻辑随写路径退役」前提不成立；M58c 改为「结论回写 + 消费链保留 + Skill 措辞对齐」 |
| FS 模式不解析 @mention：`write_round_comment` 纯文件写，`map_fs` 包 grep mention 零命中；`fs_topic_as_detail` 恒填 `unresolved_mentions=[]`；`derive_work` 仅 pending_topic_reply / round_ack_pending | `sdk/python/map_fs/parser.py:449-497,526-566`、`server/services/fs_source_service.py:170-173,323` | 不补 FS mention 投影的依据：@唤醒主链路已由 `topic.round_advanced` 自动唤醒替代（WAKEABLE_NOTIFICATION_EVENTS 含之），无需 host 手动 @；reviewer 进入话题的 @mention 路径表述在 M58a 改写时同步修正 |
| 测试面：HTTP 写测试 17 文件（test_topics.py ~100+ 处为最大头、test_mentions.py、test_topic_work_items.py、test_action_items*.py ×3、test_todos.py、test_topic_notifications.py、test_topic_progress.py、test_topic_read_cursor.py、test_agent_work.py、test_work_summary.py 等，均无 fs 等价版）；CLI 层（test_cli.py ~8 处、test_topic_routing.py DB 用例 :390-444、test_archive_command.py 整文件、test_dry_run_write_commands.py 反射、test_compat.py 快照 :130、test_clear_action_template.py 模板、test_docs_consistency.py 钉死 QUICKSTART :82-95） | `tests/` | M58b 主要工作量在测试改造；perf 类直插 DB 不走端点不受影响；分批推进（CLI 层 → HTTP fixtures → 钉死类） |
| e2e_collab.py 4 处 DB 写命令：topic create(:216)、comment(:236)、close/resolve(:314-315)；show 只读保留 | `cli/e2e_collab.py` | 话题流程改走 fs 路径（fs comment / advance-round / close）；dry_run 反射扫描断言同步 |
| Skill 文档 DB 写命令分布：topic-host（SKILL.md + experiment-gate-rubric.md + host-checklist.md）、topic-participant（SKILL.md + participant-checklist.md）、map-project-collab（SKILL.md:92,98 + commands.md 7 处 + wake.md:18）；map-plugin.yaml version=0.11.0 ×2；另有 `cli/skills/` 运行时镜像 | `.cursor/skills/` | M58a 改写面清单；wake.md:18 的 mentions 行措辞需精确化（话题域枯竭 ≠ mention 功能退役，实验评论来源仍触发） |
| migrate 读依赖安全：仅 `c.get_topic(topic_id)`（GET）+ 本地 map_fs 写；migrate-from-docs 完全离线零 API | `cli/commands/topic.py:814-873`、`cli/commands/fs.py:321-402` | 两命令不受 410 影响（唯一联动点是 PATCH 归档，已决策保留） |

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M58a-1 Skill 主体改写 | topic-host / topic-participant SKILL.md + references 全面 FS 化：命令示例切 `fs comment / advance-round / close`；resolve 语义映射显式化（结论沉淀 → `fs close`，close_note 承载结论与行动项；不做结构化 action_items frontmatter 扩展——close_note 约定先行，有真实需求再立项）；rollback-round / reopen 的 FS 等价操作约定（回滚 = 删本轮 round 文件 + 核对 index.md round/participants 一致性（以 scan_plane 重扫行为为准）、重开 = 改 index status + 说明）写入 checklist；@mention 唤醒表述修正（round_advanced 主链路，@ 为辅助） | `.cursor/skills/topic-host/`、`.cursor/skills/topic-participant/` |
| M58a-2 collab Skill 与版本 | map-project-collab：commands.md 命令示例切 fs、wake.md mentions 行措辞精确化、SKILL.md 待办分区表述核对；两个 map-plugin.yaml version 0.11.0 → 0.13.0 且 **requires 0.11 → 0.12**（r1 定案：fs 三态路由为 v0.12 M56 交付，requires 停 0.11 会让升级 Skill 的旧 CLI 用户按 checklist 执行不存在的 fs 命令）；`cli/skills/` 镜像同步 | `.cursor/skills/map-project-collab/`、`cli/skills/` |
| M58a-3 文档与模板 | QUICKSTART.md 话题演示切 fs（test_docs_consistency 同步）；cli/main.py 清除动作模板（:588）切 fs 等价（test_clear_action_template 同步） | `QUICKSTART.md`、`cli/main.py` |
| M58b-1 CLI 退役 | 7 命令 + 3 命令 DB 分支改引导性错误（exit 2 + fs 等价命令或 migrate 指引，复用 M56 `_fs_transition_rejected` 引导模式）；`--storage db` 显式传参同样引导；`map topic --help` 顶层单轨化提示（typer app help epilog 或等价机制）；test_compat 快照同步 | `cli/commands/topic.py` |
| M58b-2 server 退役 | POST×7 + DELETE → 410 + 引导 body（error envelope 模式，指明 fs 等价端点或 migrate）；PATCH 保留（archived 供 migrate；title/pinned 引导）；表与数据不删 | `server/api/topics.py` |
| M58b-3 测试改造 | **按断言语义三类执行（r1 定案）**：(a) 写端点行为断言→改 410+引导 body 断言（test_topics.py 写用例主体、test_cli.py / test_topic_routing.py / test_archive_command.py DB 用例）；(b) 消费者断言→直插 DB fixtures 保留（waker 桶、Web 投影、thread_activity、work summary 等读路径断言）；(c) 话题域 mention/work-item 产生断言→改 410 断言或移除（test_mentions.py 话题评论→mention 用例、test_topic_work_items.py 产生用例），实验域产生断言保留为主验证面；禁止直插造数假绿保留已死链路断言；test_topics.py close/resolve→action_items 级联语义改断言 service 级联函数；dry_run 反射/模板/快照/文档钉死类同步；e2e_collab.py 走 fs 并手跑一轮记录输出摘要 | `tests/`、`cli/e2e_collab.py` |
| M58c 结论回写 | PRD v0.13 风险表 mention 行带已决标注（消费者活跃/实验来源保留/话题域枯竭/不补投影的依据）；F2 行补「已落地」；Skill mention 表述终检 | `docs/prd/v0.13.md` |

## 实现顺序

1. M58a 全部（Skill + 镜像 + QUICKSTART + 模板）——硬前置，文档先于代码
2. M58b-1 CLI 引导 → M58b-2 server 410（同批，wire 层一次对齐）
3. M58b-3 测试改造（分批：CLI 层 → HTTP 17 文件 → e2e/钉死类；每批跑 fast-gate）
4. M58c PRD 回写 + Skill 终检
5. 全量 fast-gate + CLI/server 实测（evidence 2/3）+ 提交

## 风险与对策

- **测试面大（17 HTTP 文件 + 6 CLI 文件）**：分批改造每批过门控；**改造按断言语义三类执行（r1 定案，防假绿）**——(a) 写端点行为断言改 410+引导 body；(b) 消费者断言（waker 桶/Web/thread_activity/work summary）直插 DB fixtures 保留（表未删，造数合法）；(c) 话题域产生断言改 410/移除、实验域产生断言保留为主验证面；禁止直插造数保留已死链路断言（直插 Mention 行断言消费者正常 ≠ 话题评论还会产生 mention）
- **PATCH 端点双用途**：保留端点、限制语义（archived 子字段供 migrate；title/pinned 引导），test_topic_migrate.py 全 stub 不受影响，补一条 wire 级回归锁定 migrate 归档路径
- **mention 误伤**：消费链零改动；M58c 只回写结论与措辞；~39 个 mention 测试中话题域产生用例按 (c) 类改 410/移除（非直插假绿），实验域产生用例与消费者用例按 (b) 类保留
- **Skill 改写后被唤醒 agent 撞墙**：M58a 硬前置先行；CLI 引导文案给出 fs 等价命令全文（不只说「已退役」）
- **e2e_collab 改造回归**：dry_run 反射断言同步；手跑一轮 e2e 并将输出摘要记入实验日志（r1 建议采纳）
- **rollback-round / reopen 等 fs 侧无直接等价物**：引导文案按操作性质分流（写操作 → fs 等价或手动文件操作约定（M58a-1 已写入文档，含 index.md 一致性核对步骤）；存量迁移 → migrate）；DELETE 仅 server 端点（CLI 无此命令）

## 开放问题

1. DELETE /topics/{id} 去留：本 plan 决策一并 410（话题域写入口全退役原则，PRD 8 命令表未列属遗漏而非保留意图；CLI 无对应命令，无暗门风险）——需评审确认
2. resolve 结构化 action_items 的 `fs close` frontmatter 扩展：本 plan 建议不做（close_note 约定承载，PRD 留评审定夺）
3. PATCH 保留面：最小保留（仅 archived）还是整端点保留：本 plan 建议最小保留（title/pinned 引导）——评审定夺
