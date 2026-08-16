# M56 Round 1 实施日志（2026-08-16）

执行者：host（实验 c5a7aa35-da75-4e3c-b03e-a69f1d537782，plan v2 = 7c4d2990）

## 交付清单（对照 plan v2 改动范围）

### M56A 六命令接入路由 ✅

- `cli/commands/topic.py`：resolve / rollback-round / reopen / dismiss / read / mark-seen 六命令 `--id` 从 `uuid.UUID` 改 `str` + 新增 `--storage` 选项（复用 `_STORAGE_HELP`），命令体内 `_resolve_topic_ref` 解析后 DB 分支调用原 SDK 方法，与既有 show / comment / advance-round / close 四命令模式完全一致，零新解析逻辑。

### M56B fs 目标降级分类 ✅

- 新增 `_FS_TRANSITION_HINTS` 表 + `_fs_transition_rejected`（状态变迁类：exit 2 + 可执行提示）+ `_fs_projection_noop`（通知投影类：exit 0 + no-op 提示）两个统一辅助函数。
- 提示文案按 plan v2：resolve → 指向 `map topic close`（点明 `close_reason` 承担 decision 角色）；rollback-round → 提示手动管理 round 文件；reopen → 提示编辑 `map/topics/<slug>/index.md` status；dismiss / read / mark-seen → 提示 FS pending 项靠写 round 文件清理（含 `map fs work` 指引），不静默。

### M56C help 文本统一 ✅

- 六命令 `--id` help 统一为 "Topic UUID (DB), FS uuid5 id, or slug."（与既有四命令同文案）；fs 目标行为说明放命令 docstring（不进 --id help，保 10 命令文案一致性断言成立）。
- `archive --id` help 补 DB-only 标注（"archived is a DB-record flag; FS topics have no archive concept"）；`migrate` 原有 "DB topic UUID to migrate." 已合规，未动。

### M56D cancel CLI 封装 ✅

- `cli/commands/experiment.py`：新增 `experiment cancel --id`（`_rid` 短 id 解析复用，docstring 标注 creator-only + running/review 阶段 + 状态机拒绝透传），调 SDK `cancel_experiment`（client.py:504，签名核验一致）。
- `cli/map_command_client.py`：`("experiment", "cancel")` 登记进 `_WRITE_COMMANDS_2` 写命令白名单（dry-run 安全网守卫测试强制要求，未登记即测试红）。
- 文档落点按 r2 修订：不引用不存在的 docs/commands.md，docstring 自明。

### M56E 测试 ✅

- `tests/test_topic_routing.py` 扩展 18 个新用例（原 15 → 33 全绿）：
  - TestSixCommandFsDegradation（7）：三类 no-op exit 0、三类拒绝 exit 2 + 提示断言、`--storage fs` 显式覆盖降级；api_stub.calls 恒空反证 fs 路由零 API 调用。
  - TestSixCommandDbBranch（6）：六命令 DB uuid 路由（stub 记录 SDK 调用参数）、read/mark-seen 共享 mark_topic_read、DB slug 落 DB 分支。
  - TestIdHelpConsistency（3）：三态组 10 命令 --id help 逐字一致、DB-only 组标注存在、topic_app 命令分组闭合（防未来新增命令游离）。
  - TestExperimentCancelCli（2）：成功路径 + 状态机拒绝（error_code/hint 信封）透传 exit 1。
- 实现细节：_RecordingStub 返回纯字符串 dict（`_run` 的 yaml 渲染不认 SimpleNamespace / 裸 UUID，第一版 14 处 RepresenterError 后修正）；fs 测试也挂 `_client_ctx` stub（`_run` 在进 action 前构建 client，测试环境无 token）。

## 实施中发现并修复的存量缺陷（wire 阶段）

### 双重 cancel 返回 500 → 修复为 422（server/main.py）

- 发现过程：cancel 幂等性实测（evidence 第 3 条）对已 cancelled 实验 1db109ca 再次 cancel，返回 `Error 500: Internal Server Error`，而非预期的 422 状态机错误。
- 根因：`phase_service.cancel_experiment` 直接调用 `validate_phase_transition`，其抛出的是 domain 层裸 `StateMachineError`（server/domain/state_machine.py:8），而 `register_domain_exception_handlers` 的映射表只登记了 `server.services.errors.StateTransitionError`——裸 StateMachineError 无 handler → FastAPI 兜底 500。受影响面不止 cancel：submit-review / approve / start / withdraw 等所有直接调用 `validate_phase_transition` 的转换点在非法转换时全部 500。此前无 API 级测试覆盖（grep 全 tests 零命中「terminal phase」断言），故存活至今。
- 修复：映射表加一行 `StateMachineError: status.HTTP_422_UNPROCESSABLE_ENTITY`（与 StateTransitionError 同语义，无 error_code 装饰）。
- 回归锁：`tests/test_experiments.py::test_cancel_twice_returns_422_not_500`（slow 套件，API 级：首次 200 → 二次 422 "terminal phase"）。
- wire 验证：DB 副本 + 临时端口 8017 起新代码实例，SDK 直连 cancel 已 cancelled 实验 → `422 "Cannot transition from terminal phase 'cancelled'"`。8001 线上进程为旧代码，重启后生效（与 M55 E8 修复同等待重启状态，见 log-r0）。

## wire 实测记录（evidence 第 3 条）

| 场景 | 命令 | 结果 |
|------|------|------|
| fs no-op | `topic dismiss --id v012-ergonomics-review` | No-op 提示，exit 0 ✅ |
| fs no-op | `topic read --id v012-ergonomics-review` | No-op 提示，exit 0 ✅ |
| fs 拒绝 | `topic resolve --id v012-ergonomics-review --file plan.md` | exit 2 + close 提示（含 close_reason） ✅ |
| fs 拒绝 | `topic rollback-round --id v012-ergonomics-review` | exit 2 + round 文件提示 ✅ |
| fs 拒绝 | `topic reopen --id v012-ergonomics-review` | exit 2 + index.md 提示 ✅ |
| DB uuid | `topic read --id 19e0d9cc-…`（8001 实弹） | mark_topic_read 200，exit 0 ✅ |
| DB uuid | `topic dismiss --id 19e0d9cc-…`（8001 实弹） | dismiss 200，exit 0 ✅ |
| cancel 幂等 | `experiment cancel --id 1db109ca…`（旧进程 8001） | 500（修复前复现）→ 新代码 8017 验证 422 ✅ |
| cancel 未知 id | `experiment cancel --id 00000000-…dead` | 404 not found，正确报错 ✅ |

## 门禁证据

- M56 单文件：`tests/test_topic_routing.py` 33 passed（15 旧 + 18 新）
- fast-gate 全量：473 passed, 2 skipped, 1131 deselected（含新 cancel 白名单登记后的 dry-run 守卫）
- 实验域 slow 套件（test_experiments + test_state_machine + test_direct_mode + test_m55_error_envelope）：36 passed
- ruff check（server/ tests/ cli/）：All checks passed
- mypy strict 门控（server 模块，test_eng_mypy_strict_experiments_projects 等）：passed（cli 为 baseline 不在 strict 名单）

## 工程纪律记录

- ruff format 整文件格式化引入无关 churn（HEAD 上这四个文件本就非 format-clean，仓库只强制 ruff check）→ 回退后精确重放改动，最终 diff 收敛：experiment.py +11 / topic.py +148-19 / map_command_client.py +1 / test_topic_routing.py +279。
- 临时验证资源清理：8017 端口实例已 kill，DB 副本 /tmp/m56-wire-verify.db 已删除。
- 本轮无生产 DB 误写（吸取 M55 事故教训：临时实例全程使用 `MAP_DATABASE_URL` 前缀 + DB 副本）。

## 非阻塞建议落实情况（reviewer r1/r2）

- help 断言分组细化（跳过无 --id 命令 + 分组闭合守卫）：已落实为 TestIdHelpConsistency.test_topic_command_groups_are_closed。
- resolve fs 提示点明 close_reason：已落实（提示文案 + docstring）。
- no-op 提示统一模板：已落实（_fs_projection_noop 单一来源）。
