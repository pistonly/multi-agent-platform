# 实验计划：v0.7 P3 — CLI `topic archive` / `experiment archive` 薄包装

## 背景

status_md v0.7 backlog 第 1 项：**v0.7 P3 — CLI `topic archive` / `experiment archive` 命令（薄包装层）**。

目前服务端已支持通过 `PATCH /topics/{id}` / `PATCH /experiments/{id}` 携带 `archived: true` 实现归档（`archived_at` 写入），SDK 也已暴露 `update_topic` / `update_experiment`，但 CLI 仍缺少对应入口。

人工调用需要拼 HTTP + 鉴权 + JSON，门槛高、易出错。本次实验只做 **薄包装**：在 `cli/main.py` 的 `topic_app` / `experiment_app` 下新增 `archive` 子命令（含 `--undo` 反归档），把现有 SDK 调用翻译成 CLI flag，**不改 API、不扩权限模型、不动数据库**。

两轮讨论结论（已在 Round 1/Round 2 Summary 沉淀）：
- 范围锁定为 **CLI + 测试 + 文档**；后端 / 数据库 / 权限保持不动
- 设计风格对齐既有 `topic close` / `topic reopen` / `experiment approve` 等子命令
- 归档 = 可逆操作（list 默认过滤，show 仍可见，--undo 恢复），不做破坏性动作

## 目标

1. 提供 `map topic archive --id <uuid>` 与 `map experiment archive --id <uuid>` 子命令
2. 提供 `--undo` / `--unarchive` 反归档参数（两者为同义别名，`--help` 同时展示）
3. 输出成功 / 失败 / 幂等同物的统一 JSON / 文案
4. 补 CLI 单元 / 集成测试、文档、`map --help` 友好提示
5. **不引入交互确认**：`archive` 与 `--undo` 都是可逆操作（undo 即恢复），不存在不可逆销毁，因此本期**不提供 `--yes` flag**（与 `topic close` / `experiment approve` 一致，不弹确认）

## 非目标

- 不新增 / 修改服务端 API 端点（已存在 `PATCH /topics/{id}`、`PATCH /experiments/{id}`）
- 不修改 `archived_at` 字段语义、不引入级联归档（如归档 topic 同时归档其下 experiment）
- 不动权限模型与角色判定（保持现有 `creator_agent_id` / `project_id` 校验）
- 不在 Web UI 增加对应按钮（v0.7 仅 CLI；UI 列入 v0.8 候选）
- 不做批量归档（如 `map topic archive --status closed --older-than 30d`），本期仅单 ID 操作
- 不引入交互确认 prompt（archive 可逆，不需要二次确认）

## SDK schema 现状（前置验证）

执行实施步骤 1 前的核对结论（已通过 `grep sdk/python/map_types/schemas.py` 验证）：

- `TopicUpdate.archived: bool | None = None` 已存在（`schemas.py:292`）
- `ExperimentUpdate.archived: bool | None = None` 已存在（`schemas.py:184`）
- 服务端 `PATCH /topics/{id}` / `PATCH /experiments/{id}` 已接受 `archived` 字段并写入 `archived_at`（`server/api/topics.py:97`、`server/api/experiments.py`）

**结论：实施步骤 1 改为纯验证步骤**，**不修改** `schemas.py`，避免无意义改动。如未来服务端 schema 退化，需在 PR 描述里再补字段并标注兼容性。

## 验收标准

1. **CLI 注册**：`map topic --help` 与 `map experiment --help` 中可见 `archive` 子命令，且帮助文本展示 `--undo, --unarchive` 两个 flag（typer 自动从变量名生成两个 alias）
2. **正常归档**：`map topic archive --id <open topic uuid>` 成功调用 PATCH，返回 `archived_at` 时间戳；再次 list 时（默认 `include_archived=False`）该 topic 不再出现
3. **反归档**：`map topic archive --id <archived uuid> --undo` 撤销归档；list 中重新可见
4. **`--undo` / `--unarchive` 同义**：两种写法行为完全一致（测试 `test_undo_and_unarchive_equivalent`）；同时使用 `--undo --unarchive` 报参数冲突或以先出现者为准（typer 默认行为），至少行为可预测
5. **幂等**：对已归档对象执行 archive（无 `--undo`）**写入同一 `archived_at`**（与 SDK `update_topic` PATCH 语义一致）。测试用例 `test_archive_idempotent_returns_same_timestamp`：mock 服务端 PATCH 响应记录两次 archive 返回相同 `archived_at`，断言相等
6. **错误处理**：
   - `--id` 缺失：typer 自动报错 `Error: Missing option '--id'`，exit code ≠ 0
   - `--id` 非法 UUID：typer 自动报错 `Error: Invalid value for '--id': 'xxx'`，exit code ≠ 0
   - 不存在的 ID（404）：CLI 输出 `Error: topic <uuid> not found`（`experiment` 同理），exit code ≠ 0；该文案通过包装 `_run()` 中的 `MAPHTTPError`（status_code == 404）特判实现，不依赖服务端 message
   - `--undo` 与 archive 语义互不冲突（archive 默认走归档分支，`--undo` 走反归档分支，二者只在反向上切换 `archived` 值，不互斥抛错）
7. **SDK 复用**：CLI 内部直接调用现有 `MAPClient.update_topic` / `update_experiment`，不复刻 HTTP 调用逻辑；`TopicUpdate(archived=...)` / `ExperimentUpdate(archived=...)` 使用 `exclude_unset=True` 序列化（沿用 `client.py:526` 先例）
8. **show 可见性**：`map topic show --id <archived uuid>` / `map experiment show --id <archived uuid>` 仍能正常返回对象，且 payload 含 `archived_at` 字段；list 默认过滤但 show 不受影响（避免 list 过滤后用户无法查阅归档对象造成困惑）
9. **权限矩阵**：任何项目内已认证 Agent（host / participant / reviewer）均可在项目内对 topic / experiment 执行 archive / undo（沿用现有 `perm.ensure_topic_access`，不新增角色判定）；测试 `test_archive_permission_matrix` 通过切换 agent token 覆盖至少两条 persona 的成功路径
10. **测试**：`pytest tests/cli/test_archive_command.py` 覆盖：
    - `test_topic_archive_basic_success`
    - `test_topic_archive_undo_success`
    - `test_experiment_archive_basic_success`
    - `test_experiment_archive_undo_success`
    - `test_undo_and_unarchive_equivalent`
    - `test_archive_idempotent_returns_same_timestamp`
    - `test_archive_not_found_404_friendly_message`
    - `test_archive_invalid_uuid_typer_error`
    - `test_archive_missing_id_typer_error`
    - `test_archive_permission_matrix`（mock 不同 agent token）
    - `test_show_after_archive_still_visible_with_archived_at`
    - 全部使用 mock httpx，不依赖外部状态
11. **文档**：
    - `docs/CLI.md` 新增 `topic archive` / `experiment archive` 章节，含示例、权限说明、可逆性提示
    - `cli/main.py` typer docstring 写清"thin wrapper around PATCH /topics/{id} archived"
    - `map topic archive --help` 帮助文本含示例（typer docstring 末尾 `Examples:` 段）
    - `AGENTS.md` 提及"归档话题 = list 默认隐藏，show 仍可见，反归档恢复"
12. **不破坏既有命令**：`map topic list` / `map experiment list` 行为不变（仍默认过滤已归档）
13. **审计可追溯性（best-effort）**：archive / undo 后调用 `get_audit_history` 检查是否产生 `type=archived` / `type=unarchived` 条目：
    - 若服务端已实现：在测试 `test_archive_emits_audit_event` 中断言存在对应条目
    - 若服务端尚未实现：在 plan 的"后续"中记录为 backlog，不阻塞本期合并；测试降级为"best-effort 跳过（xfail）"

## 实现步骤

1. **SDK schema 验证（纯 read-only）**：`grep -n "archived" sdk/python/map_types/schemas.py` 确认 `TopicUpdate.archived` 与 `ExperimentUpdate.archived` 均为 `bool | None = None`；已确认存在，**不修改** `schemas.py`
2. **新增 CLI 子命令**（`cli/main.py`）：
   - `@topic_app.command("archive")`：参数 `--id`（必填，`uuid.UUID`）、`--undo`（`bool = False`）、`--unarchive`（`bool = False`，typer 自动作为 `--undo` 的别名展示在 help 中；实现层用 `archive = not (undo or unarchive)` 统一判定）
   - `@experiment_app.command("archive")`：同上
   - 内部构造 `TopicUpdate(archived=<bool>)` / `ExperimentUpdate(archived=<bool>)` 调 `c.update_topic(...)` / `c.update_experiment(...)`
   - 404 错误友好包装：在 `_run()` 之后或单独 helper `_run_archive()` 中捕获 `MAPHTTPError`，对 `status_code == 404` 输出 `Error: topic <uuid> not found` / `Error: experiment <uuid> not found`；其他 status code 沿用现有 `Error {status_code}: {detail}` 文案
3. **输出格式**：沿用现有 `_run()` 风格；成功后 `_print_json` 打印完整对象；可附加人类可读前缀（与 `_print_warnings` 类似，非强制）
4. **错误透传**：404 特判，其余 `_run()` 已统一处理 exit code
5. **测试**（`tests/cli/test_archive_command.py`）：
   - 单测：CLI 参数解析、`TopicUpdate(archived=...)` 构造正确、`--undo` 与 `--unarchive` 同义
   - 集成（mock httpx）：覆盖验收标准 10 列出的全部用例
6. **文档**：
   - `docs/CLI.md` 新增章节 + 示例
   - `cli/main.py` 中 `typer` docstring 写清"thin wrapper around PATCH /topics/{id} archived"，末尾 `Examples:` 段
   - `AGENTS.md` 提及"归档话题 = 列表默认隐藏，show 仍可见，反归档恢复"
7. **手动验证**：本地起服务（`docker compose up`）：
   - **新建临时测试 topic**：`map --persona host topic create --title "archive-cli-smoke-<timestamp>"`（不复用现有 open topic，避免污染 status 实验流）
   - `map --persona host topic archive --id <新 topic>`
   - `map --persona host topic list` 确认新 topic 不出现
   - `map --persona host topic list --include-archived` 确认可见
   - `map --persona host topic show --id <新 topic>` 确认返回对象含 `archived_at`
   - `map --persona host topic archive --id <新 topic> --undo` 反归档
   - 验证完成后无需清理（已是 open 状态，与原 list 表现一致；如需彻底清理可 `map --persona host topic archive --id <新 topic>` 归档封存）

## 风险

- **schema 兼容性**：SDK schema 已包含 `archived` 字段（已验证），无新增 schema 风险；序列化沿用 `exclude_unset=True`（先例：`client.py:526`）
- **权限**：固化边界为"项目内任何已认证 Agent 均可 archive / undo"，沿用 `perm.ensure_topic_access`；不引入 host/participant/reviewer 差异
- **幂等语义**：明确"重复 archive = 同时间戳幂等成功"（与 SDK PATCH 语义一致），无需服务端特殊支持
- **误归档恢复**：归档非删除、可 `--undo` 恢复；show 命令不受 list 默认过滤影响；文档需明确"archive ≠ delete"，避免用户误以为删除
- **审计可追溯**：服务端当前 `update_topic` / `update_experiment` 路径**未确认**是否会写 audit 事件；若不写，验收标准 13 降级为 best-effort / xfail，作为 backlog 记录

## 回滚

- 仅 `cli/main.py` 子命令 + 测试 + 文档；如发布失败可直接 `git revert` 单 commit
- 不动服务端、不动数据库 schema、不动权限；回滚后无残留状态

## 后续（带入实验或 backlog）

- 批量归档（`--status closed --older-than Nd`）→ v0.7 P4 候选
- Web UI 归档按钮（仅 CLI 体验闭环，UI 推迟）→ v0.8 候选
- 级联归档（归档 topic 时同步归档其下 experiment）→ 待业务明确是否需要，本期不做
- 服务端 archive / undo 写 audit 事件（如尚未实现）→ 单独实验，单独评审
- 是否引入交互确认 / `--yes`（若未来新增不可逆操作如 delete，再单独评估）→ backlog
