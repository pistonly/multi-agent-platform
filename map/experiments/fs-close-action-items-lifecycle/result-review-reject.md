# FS close_note 的 action_items 解析入跟踪 — 结果审批（驳回）

## 结论

**驳回**（唯一理由：收口缺失）。工作区实现的**功能正确性已核证成立**，但结果未提交进任何 commit——git HEAD（`a939d7f`）中不存在本实验 A1-A7 的任何代码改动，全部实现在工作区未收口。按审批纪律（结果对照 git HEAD 而非工作区；M62 先例：文件正确但未提交进收口 commit = 驳回），本结果不能 approve。

## 核证记录（供 host 修正后快速复审）

以下核证均为**工作区**（未收口）状态，works 作为「补提交后即时达标」的判据，不作为审批通过依据：

| v3 验收 | 工作区实现 | 判定(工作区) |
|---------|-----------|--------------|
| A1 yaml 事实源 + parser | `sdk/python/map_fs/parser.py`：`_ACTION_ITEMS_FILE`/`parse_action_items_file`(357)/`read`/`write_action_items`(706/718, `_atomic_write`+safe_dump)/`FsTopic.action_items` 挂载(499) | ✅ |
| A2 投影义务 | `server/services/fs_source_service.py:1228` `kind="action_items"`、`idempotency_key="fs:action_item:{slug}:{id}"`(1236)、`clear_action="complete_or_cancel_action_item"`(1237)、owner 精确路由、stale nudge 不发(无非派生义务且无 open 项) | ✅ |
| A3 close 门禁 | `FsOpenActionItemsError`(103)、`validate_fs_close` 409 分支(1602-1605)、`server/api/fs.py:301-305` `action_items_open` 映射 + 直接 /close 端点 catch(636) | ✅ |
| A4 complete 带证据 | `cli/commands/topic.py` `topic action-item` 组：`complete --evidence` 空证据 exit 2 拒绝、`cancel --reason`、`add`、`list`；纯本地写 + maybe_auto_sync | ✅ |
| A5 cancel 语义 | cancel 写回 status=cancelled + reason，不挡 close | ✅ |
| A6 Skill 文档 | `host-checklist.md` §3b 收敛时落盘 + close_note 模板移除 action_items 段；wake.md kind 表加 action_items；topic-host/map-project-collab SKILL 同步 | ✅ |
| A7 投影缓存兼容 | projection 回退分支同源(三视角转换器 fs/projection/as_fs_topic 均 marshal action_items) | ✅ |
| 测试面 | **复跑 tests/test_fs_action_items.py 20 passed**(13.1s, 非仅 log 声称)；log 报合并回归 150 passed / 0 failed；`ruff` 声称过 | ✅ |

**额外发现（live 传输修复，非本实验验收项但值得记录）**：`_render_validate_error` 顺带修复既有 round_ack_pending live 降级（`map_client.client.py:183` 把 error detail `str()` 成 repr 字符串，原物化 dict 分支失效）——该修复同时解决了既有 ack 409 的 live 渲染，属合理附带。

## 驳回理由（具体）

1. **git HEAD 无本实验实现**：`git status` 显示 13 个功能文件（`cli/commands/fs.py`/`cli/commands/topic.py`/`sdk/python/map_fs/parser.py`/`sdk/python/map_types/schemas/fs.py`/`server/api/fs.py`/`server/services/fs_source_service.py` + 5 个 Skill 文档）+ 新增 `tests/test_fs_action_items.py` 全部未提交（M/??）；相关文件最近 commit 仍为 `4e45f4e`（上一实验 advance-ack），HEAD=`a939d7f`(仅 v3 修订记录归档)。
2. **收口 commit 缺位**：本实验无任何 `I1-I6` 对应的代码 commit；`map/experiments/fs-close-action-items-lifecycle/` 下只有 plan/evidence/log（均为文档），evidence.yaml 的 pytest_summary 也未附 commit_sha。
3. **对照验收**：A3/A7 的「投影义务 + 门禁 + close 路径」落在 server/CLI 运行时代码，未经 commit 平台运行时不可见——即便工作树文件存在，部署的 daemon（18400）加载的是 HEAD/已装代码，实际不具备本实验能力。

## 要求 host 修正

- 补一个（或按 I 粒度分多个）**收口 commit**：至少覆盖 `cli/commands/fs.py`、`cli/commands/topic.py`、`sdk/python/map_fs/parser.py`、`sdk/python/map_fs/__init__.py`、`sdk/python/map_types/schemas/fs.py`、`server/api/fs.py`、`server/services/fs_source_service.py`、`tests/test_fs_action_items.py`、`tests/cli/test_fs_persona.py`、`tests/test_049_project_fs_content_config.py` 及 5 个 Skill 文档；保留窄提交、与并行未提交改动分离（只 stage 本实验文件）。
- 补提交后可直接再次提交结果审批；本 nuclei 的 A1-A7 工作区核证可作为即时达标的预判，审批时仅需确认 commit 已在 HEAD 且为收口。
