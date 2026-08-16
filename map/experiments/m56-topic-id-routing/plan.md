---
title: "M56 命令路由统一（v0.12 收官）"
acceptance:
  - "resolve / rollback-round / reopen / dismiss / read / mark-seen 六命令的 --id 从纯 DB UUID 扩展为三形态（DB UUID / FS uuid5 / slug），新增 --storage 显式覆盖选项，路由行为与既有 show / comment / advance-round / close 完全一致（复用 _resolve_topic_ref，不另写解析逻辑）"
  - "fs 目标降级行为分类落地：通知投影类（dismiss / read / mark-seen）对 fs 目标 no-op + 明确提示、exit 0；状态变迁类（resolve / rollback-round / reopen）对 fs 目标 exit 2 + 可执行提示（分别指向 map topic close / 手动管理 round 文件 / 编辑 index.md status）"
  - "migrate 与 archive 的 --id help 文本明确标注 DB-only（migrate 已有，archive 为 PRD 清单补遗：archived 语义只对 DB 记录存在，FS 无归档概念）；验收例外清单相应从「除 migrate」扩为「除 migrate / archive」"
  - "并入移交项：map experiment cancel --id 封装（SDK cancel_experiment 已有，server 端点已验证），--id 与 experiment 族一致支持短 id 前缀；对非 running/Review 阶段实验的拒绝错误原样透传（不吞状态机报错）"
  - "map topic --help 各命令 --id help 文本一致（DB-only 两命令除外）；新增 pytest：六命令 slug/uuid 路由断言、至少一个 fs 目标命令的降级行为断言、help 一致性断言；fast-gate 全量回归通过"
evidence_keys:
  - "pytest 新增 m56 路由测试全绿"
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：slug 形态操作 fs 话题（no-op 提示与 exit 2 提示各至少一条）；map experiment cancel 幂等性实测（对已 cancelled 实验报状态机错误而非静默成功）"
dependencies:
  - "v0.12 提案 reviewer 两轮核对确认（话题 v012-ergonomics-review round2 收敛 ready），M56 为 P1 收官项"
  - "M51 _resolve_topic_ref 三态路由基座已交付（实验 df22697d done），show / comment / advance-round / close 四命令先行接入，本实验复用同一解析层"
---

# M56 命令路由统一（v0.12 收官）

## 目标

按 docs/prd/v0.12.md §M56 收敛 E7：`topic` 命令族 `--id` 语义单一化。六个仍只收 DB UUID 的命令（resolve / rollback-round / reopen / dismiss / read / mark-seen）接入 `_resolve_topic_ref` 三态路由，agent 不再需要记忆两套 id 规则；顺带封装 M55 遗留移交项 `map experiment cancel`。完成后 v0.12 三里程碑收官。

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M56A 六命令接入路由 | 六命令 `--id` 参数类型 `uuid.UUID → str`，加 `--storage` 选项（help 复用 `_STORAGE_HELP`），命令体内 `_resolve_topic_ref` 解析后 DB 分支调用原 SDK 方法 | `cli/commands/topic.py`（resolve / rollback-round / reopen / dismiss / read / mark-seen 六个函数） |
| M56B fs 目标降级分类 | 通知投影类（dismiss / read / mark-seen）：fs 目标 no-op + 提示「FS 话题无 DB todos 投影动作；pending 项靠写 round 文件清理」exit 0；状态变迁类（resolve / rollback-round / reopen）：fs 目标 exit 2 + 可执行提示（resolve → 用 `map topic close`，文案点明 `close_reason` 承担 decision 角色；rollback-round → FS 轮次是文件事实，提示手动管理 round 文件；reopen → 提示编辑 `map/topics/<slug>/index.md` 的 status 字段） | 同上，新增 `_fs_target_notice` 辅助函数（dismiss/read/mark-seen 三个 no-op 提示共用同一模板生成，避免文案漂移） |
| M56C help 文本统一 | 六命令 `--id` help 统一为既有四命令同款文案（"Topic UUID (DB), FS uuid5 id, or slug."；fs 降级命令追加一句 fs 目标行为说明）；archive 的 `--id` help 补 DB-only 标注 | `cli/commands/topic.py` |
| M56D cancel CLI 封装 | 新增 `map experiment cancel --id`：调 SDK `cancel_experiment`（端点 M55 wire 验证已走通），`_rid` 短 id 解析复用；docstring 标注仅 creator 且 running/review 阶段可取消（文档不强加：CLI.md 现无命令逐一清单，docstring 自明） | `cli/commands/experiment.py` |
| M56E 路由回归测试 | 扩展 `tests/test_topic_routing.py`：六命令 slug 路由到 fs 分支（降级行为断言）与 uuid 路由到 DB 分支（stub client 断言 SDK 调用参数）；help 一致性断言（遍历 topic_app 命令，跳过无 --id 的 create/list/progress，分组断言：三态组 show/comment/advance-round/close + 新增六命令共 10 命令文案一致，DB-only 组 migrate/archive 文案一致）；cancel CLI 测试（成功 + 状态机拒绝透传） | `tests/test_topic_routing.py` + `tests/test_cli_experiments.py`（cancel 部分视现有文件归属） |

## 实现顺序

1. M56A + M56B（同一文件同一批函数，路由与降级一起落地）
2. M56C（help 文案，随 A/B 提交）
3. M56D（cancel 封装，独立小提交）
4. M56E（测试，随各子项补齐，最后全量回归）
5. CLI 实测（evidence 第 3 条）+ wire 验证

## 风险与对策

- 六命令参数类型从 `uuid.UUID` 改 `str` 是 breaking change（旧脚本传非法 uuid 字符串不再被 typer 拦截而是进路由层）：路由层对非法 uuid 走 slug 分支后报「topic not found」，错误信息含修复提示（M55 错误信封精神），回归测试锁定
- 本实验不改 topic 族短 id 支持（遵循 M54 决策：短 id 仅 experiment 族）：agent 误用 8 位短 id 时走 slug 分支报 not found，该错误提示已含修复引导（M54 交付），无需改码
- dismiss/read/mark-seen 的 fs no-op 可能掩盖真错误（agent 以为清理了 todos）：提示文本明确说明「FS 话题的 todos 投影项（如 fs_file_missing）靠写 round 文件清理，本命令无操作」，不静默
- resolve 对 fs 目标拒绝可能不符合 PRD「按需降级为 no-op」的字面表述：PRD 原文「fs 目标按需降级为 no-op + 提示」中的「按需」允许裁量；本 plan 将降级二分为「通知投影类 no-op / 状态变迁类报错」，理由是 resolve/rollback/reopen 的 fs 等价操作不存在于 API（SDK 仅 fs_advance_round / fs_close_topic），静默 no-op 会让 agent 误以为收敛已发生。此设计决策提请 reviewer 确认
- cancel 封装与现有 experiment 状态机耦合：不新增状态，仅透传 server 状态机错误（422/409 原样渲染，M55E 的 hint 渲染自动生效）

## 开放问题（提请评审）

1. **archive 的 PRD 清单遗漏**：PRD M56 表格未列 archive（同为 DB UUID-only）。本 plan 按「archived 语义只对 DB 记录存在（PATCH archived=true），FS 话题无归档概念」将其归入 DB-only 标注组，验收例外清单扩为「除 migrate / archive」。若 reviewer 认为应接入路由（fs 目标报错或映射 close），请提出
2. **`experiment log --log-file-path` 瘦身形态不并入**：`ExperimentLogCreate` schema 无 `file_path` 字段，需 server schema + DB 列 + 校验三处改动，与 M56「CLI 路由收口」主题正交；建议延后 v0.13（与 FS 话题-实验关联缺口一并规划）。M55 移交项仅并入 cancel
3. **fs 状态变迁类命令的 exit code**：本 plan 定 exit 2（用法类错误，typer 惯例）。若 reviewer 倾向 exit 1（运行时错误）请指出

## 验收演示脚本

```bash
# 六命令三形态路由：slug 直接操作 fs 话题
map --persona host topic dismiss --id v012-ergonomics-review   # no-op + 提示, exit 0
map --persona host topic read --id v012-ergonomics-review      # no-op + 提示, exit 0
map --persona host topic resolve --id v012-ergonomics-review --file x.md  # exit 2 + 可执行提示
# DB 话题需完整 uuid 或 slug（topic 族不支持 8 位短 id，遵循 M54 决策）
map --persona host topic read --id <完整-DB-uuid>
# cancel 封装
map --persona host experiment cancel --id <uuid>
```
