# 实验计划：v0.7 P3 — CLI `topic archive` / `experiment archive` 薄包装

## 背景

status_md v0.7 backlog 第 1 项：**v0.7 P3 — CLI `topic archive` / `experiment archive` 命令（薄包装层）**。

目前服务端已支持通过 `PATCH /topics/{id}` / `PATCH /experiments/{id}` 携带 `archived: true` 实现归档（`archived_at` 写入），SDK 也已暴露 `update_topic` / `update_experiment`，但 CLI 仍缺少对应入口。

人工调用需要拼 HTTP + 鉴权 + JSON，门槛高、易出错。本次实验只做 **薄包装**：在 `cli/main.py` 的 `topic_app` / `experiment_app` 下新增 `archive`（及 `--undo` 反归档）子命令，把现有 SDK 调用翻译成 CLI flag，**不改 API、不扩权限模型、不动数据库**。

两轮讨论结论（已在 Round 1/Round 2 Summary 沉淀）：
- 范围锁定为 **CLI + SDK + 测试 + 文档**；后端 / 数据库 / 权限保持不动
- 设计风格对齐既有 `topic close` / `topic reopen` / `experiment approve` 等子命令
- 默认仅作用于未归档对象；已归档对象二次 archive 直接幂等成功（或返回明确提示，二选一，见开放问题）

## 目标

- 提供 `map topic archive --id <uuid>` 与 `map experiment archive --id <uuid>` 子命令
- 提供 `--undo` / `--unarchive` 反归档参数，对称设计
- 提供 `--yes` 跳过交互确认（脚本场景）
- 输出成功 / 失败 / 幂等同物的统一 JSON / 文案
- 补 CLI 单元 / 集成测试、文档、`map --help` 友好提示

## 非目标

- 不新增 / 修改服务端 API 端点（已存在 `PATCH /topics/{id}`、`PATCH /experiments/{id}`）
- 不修改 `archived_at` 字段语义、不引入级联归档（如归档 topic 同时归档其下 experiment）
- 不动权限模型与角色判定（保持现有 `creator_agent_id` / `project_id` 校验）
- 不在 Web UI 增加对应按钮（v0.7 仅 CLI；UI 列入 v0.8 候选）
- 不做批量归档（如 `map topic archive --status closed --older-than 30d`），本期仅单 ID 操作

## 验收标准

1. **CLI 注册**：`map topic --help` 与 `map experiment --help` 中可见 `archive` 子命令
2. **正常归档**：`map topic archive --id <open topic uuid>` 成功调用 PATCH，返回 `archived_at` 时间戳；再次 list 时（默认 `include_archived=False`）该 topic 不再出现
3. **反归档**：`map topic archive --id <archived uuid> --undo` 撤销归档；list 中重新可见
4. **幂等**：对已归档对象执行 archive（无 `--undo`）应当成功（写入同一 `archived_at`）或返回明确 `"already archived"` 提示；测试固定一种行为
5. **错误处理**：
   - `--id` 缺失 / 非法 UUID：友好报错，exit code ≠ 0
   - 不存在的 ID：透传服务端 404，CLI 打印明确错误，exit code ≠ 0
   - `--undo` 与 archive 语义互不冲突
6. **SDK 复用**：CLI 内部直接调用现有 `MAPClient.update_topic` / `update_experiment`，不复刻 HTTP 调用逻辑
7. **测试**：`pytest tests/cli/test_archive_command.py` 覆盖正常归档 / 反归档 / 幂等 / 错误路径，且 mock 服务端确保不依赖外部状态
8. **文档**：`docs/CLI.md` 增加 `topic archive` / `experiment archive` 章节；`map topic archive --help` 帮助文本含示例
9. **不破坏既有命令**：`map topic list` / `map experiment list` 行为不变（仍默认过滤已归档）

## 实现步骤

1. **盘点 SDK 现状**：确认 `MAPClient.update_topic` / `update_experiment` 已支持 `archived` 字段；如 schema 缺失则补 `TopicUpdate.archived: bool | None`（**非破坏性**，仅可选字段）
2. **新增 CLI 子命令**（`cli/main.py`）：
   - `@topic_app.command("archive")`：参数 `--id`（必填）、`--undo`（默认 False）
   - `@experiment_app.command("archive")`：同上
   - 内部构造 `TopicUpdate(archived=not undo)` / `ExperimentUpdate(archived=not undo)` 调 `c.update_topic(...)` / `c.update_experiment(...)`
3. **输出格式**：沿用现有 `_run()` 风格；成功打印 `"archived: <id> at <archived_at>"` 或 `"unarchived: <id>"`
4. **错误透传**：`_run()` 已统一处理 exit code；仅确认 404 错误信息对用户友好
5. **测试**（`tests/cli/test_archive_command.py`）：
   - 单测：CLI 参数解析、`TopicUpdate` 构造正确
   - 集成（mock httpx）：正常 archive / undo / 幂等 / 404 / 无效 UUID
6. **文档**：
   - `docs/CLI.md` 新增章节 + 示例
   - `cli/main.py` 中 `typer` docstring 写清"thin wrapper around PATCH /topics/{id} archived"
   - 在 `AGENTS.md` 或 `docs/WORKFLOW.md` 提及"归档话题 = 列表默认隐藏，反归档恢复"
7. **手动验证**：本地起服务（`docker compose up`），用 host persona 跑：
   - `map --persona host topic archive --id <test topic>`
   - `map --persona host topic list` 确认已归档 topic 不出现
   - `map --persona host topic list --include-archived` 确认可看到
   - `map --persona host topic archive --id <test topic> --undo` 反归档

## 风险

- **schema 兼容性**：`TopicUpdate` / `ExperimentUpdate` 是 Pydantic 模型，新增 `archived` 字段需用 `exclude_unset=True` 序列化以兼容旧调用方（已有先例，见 `client.py:526`）
- **权限**：host / participant / reviewer 三个 persona 对 archive 的权限可能不一致；本期保持"任何已登录 Agent 均可 archive 自己项目内的对象"，沿用现有 `update_topic` 鉴权，不新增
- **幂等语义选择**：明确"重复 archive = 同时间戳幂等成功"或"返回 already-archived 提示"二选一，需在 plan 中固化（本计划选前者，因与 SDK `update_topic` PATCH 语义一致）
- **误归档恢复**：归档非删除、可 `--undo` 恢复；文档需明确这一点，避免用户误以为"删除"

## 回滚

- 仅 `cli/main.py` 子命令 + 测试 + 文档；如发布失败可直接 `git revert` 单 commit
- 不动服务端、不动数据库 schema、不动权限；回滚后无残留状态

## 后续（带入实验或 backlog）

- 批量归档（`--status closed --older-than Nd`）→ v0.7 P4 候选
- Web UI 归档按钮（仅 CLI 体验闭环，UI 推迟）→ v0.8 候选
- 级联归档（归档 topic 时同步归档其下 experiment）→ 待业务明确是否需要，本期不做
