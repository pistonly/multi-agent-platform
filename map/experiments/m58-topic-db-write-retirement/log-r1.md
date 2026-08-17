# M58 实验日志 (Round 1)

**执行时间**: 2026-08-17
**执行者**: multi-agent-platform-host
**结果**: 通过（全部验收证据落地；全量 slow 回归中 M58 相关失败清零，剩余失败均为预先存在的测试债；真实多 agent e2e 九步闭环补跑完成，见备注）

## 代码落地

### M58a Skill 单轨化（fs 指令 + 存量引导）

| 文件 | 改动 |
|------|------|
| `.cursor/skills/topic-host/SKILL.md` + `cli/skills` 镜像 | 开话题/收轮/收尾全链改 `map fs topic-create / advance-round / close --note`；`fs close --note` 承载 decision / rationale / action_items |
| `.cursor/skills/topic-participant/SKILL.md` + 镜像 | 「发言文件即 ack」表态模型；存量 DB 话题只读、待迁移后表态 |
| `.cursor/skills/map-project-collab/SKILL.md` + 镜像 | frontmatter 待办场景与 `action_items` 来源改 close notes 表述；`mentions` 行标注话题域来源随退役枯竭 |
| `.cursor/skills/experiment-reviewer/SKILL.md` + 镜像 | 过时 `topic resolve` / `advance-round --ack` 指引改为发言文件表态 + `fs close --note` 行动项处置（M58a 范围外发现的残留，一并修正） |
| `map-project-collab/references/commands.md` | `topic resolve` → `fs close --note` 承载等价表；rollback/reopen/archive 的 FS 手动操作说明 |
| `map-project-collab/references/wake.md` | kind→清理分发表：round_ack/pending_round_acks 分流 FS 写发言文件 vs 存量只读 |
| `topic-host/references/host-checklist.md`、`topic-participant/references/participant-checklist.md` | host 收尾清单 / participant 禁止对存量话题跑 DB 写命令 |
| `topic-host/map-plugin.yaml`、`topic-participant/map-plugin.yaml` | version 0.13.0 / requires 0.12 |
| `QUICKSTART.md`、`cli/main.py` 模板 | 话题写路径单轨化提示 |

### M58b-1 CLI 引导性拒绝

| 文件 | 改动 |
|------|------|
| `cli/commands/topic.py` | `_db_write_retired()` 引导（exit 2 + fs 等价命令 + `topic migrate` 提示）；create/resolve/archive 无条件引导；advance-round/rollback-round/comment/close/reopen 在 topic 解析后按 DB 分支引导（`--storage db` 显式传参同样引导） |

### M58b-2 server 写端点退役

| 文件 | 改动 |
|------|------|
| `server/api/topics.py` | 8 个写端点（create/comment/close/reopen/advance-round/rollback-round/resolve/DELETE）直接 410 `error=topic_write_retired` + 分操作 hint；PATCH 仅放行 `archived` 字段（migrate 归档通道），其余字段 410；GET 只读端点不回退 |
| `server/api/fs.py` | fs 通道不回退（advance-round 服务端校验 ack 满员后写回 index.md） |

### M58b-3 测试改造

| 文件 | 改动 |
|------|------|
| `tests/_db_topic_factory.py`（新增） | `db_create_topic` / `db_add_comment` / `db_resolve_with_action_items` DB 直插造数（补 `first_open_at` 对齐 I1/I3 service 契约） |
| `tests/cli/test_archive_command.py` | topic archive 8 用例改断言引导（exit 2 + `mv map/topics/<slug>/ map/archive/topics/`）；reviewer undo experiment 改断言 403（authz PR3 门禁，原断言被 410 掩盖） |
| `tests/test_sdk.py` | 话题域 SDK 写方法合并为单一 410 断言（含 body 序列化路径）；experiment/action_item 消费用例造数改 DB 直插 |
| `tests/test_mcp.py` | `test_mcp_topic_flow` 改断言 4 个 MCP 写工具透传 ToolError(410 + `topic_write_retired` + 分操作 fs hint) |
| `tests/test_waker_phase1_acceptance.py` | a3 三向审计联查造数改 DB 直插（topic + Notification 行） |
| `tests/test_cli.py` | resolve 引导断言改精确命令全文（`map fs close --topic <slug> --reason <code> --note <decision>`） |
| 其余 17 个 HTTP 测试文件 | 造数与断言批量切 DB 直插 / 只读端点 |

### M58b-3 e2e

| 文件 | 改动 |
|------|------|
| `cli/e2e_collab.py` | Scenario 增 `topic_slug`；create/participant comment/host reply/decide close 四处 prompt 切 `map --persona host fs topic-create / fs comment / fs close` 命令 |
| `cli/e2e_collab.py`（补跑修正） | step 6 相位断言 `approved`→`review`（评审提交只清 pending，host 显式 approve 才进 approved，对齐 `review_service.assert_approve_eligibility`）；step 7 prompt 补 `experiment approve` 指令 |

### M58d experiment create 对 FS 话题的三态路由修复（e2e 补跑发现的 blocker）

e2e 补跑在 host-decide-experiment 步暴露：`experiment create --topic-id <FS uuid5>` 报 404——`project_service.create_experiment / create_experiment_warnings` 两处裸 `db.get(Topic, ...)` 未走 M56 三态路由，且 `Experiment.topic_id` 上的 FK 使 FS 话题 id 落库必被拒。

| 文件 | 改动 |
|------|------|
| `server/domain/models.py` | `Experiment.topic_id` 退役 `ForeignKey("topics.id")`（保留 index）；删除 `Experiment.topic` / `Topic.experiments` 双向关系（全仓核证无真实引用，仅 MagicMock 桩触及）；测试库走 `create_all` 自动无 FK |
| `alembic/versions/046_experiment_topic_fk_retirement.py`（新增） | drop `fk_experiments_topic_id`（幂等 guard + PG `drop_constraint` / SQLite `batch_alter_table` 双方言，042 同范式）；downgrade 重建。dev 库 045→046 已应用并核验 |
| `server/services/project_service.py` | `create_experiment`：DB 命中走原校验；miss 时 `fs_svc.find_fs_topic_by_id` 解析，校验 project 归属 / status（`.value` 字符串比较）/ host 门禁（`persona_short_name` 对齐 FsTopic.creator 字符串）。`create_experiment_warnings` 同步接三态路由（FsTopic.round 字符串比较 `ready`） |
| `tests/test_fs_source.py` | 新增 `test_experiment_create_on_fs_topic`（FS 话题 201 + dup 409 + ghost 404 三态断言）；既有分页测试造数改 ORM 直插（原 POST /topics 已 410，既有债一并修复） |

### M58c PRD 回写

| 文件 | 改动 |
|------|------|
| `docs/prd/v0.13.md` | F2（话题写端点退役）标注「已落地（M58 实验）」；mention 退役风险行写已决结论（消费链保留、实验域来源持续、话题域来源枯竭、不做 FS 评论 mention 投影） |

## 测试与验证

目标文件级结果：

- `tests/cli/test_archive_command.py`: **13 passed + 1 xfailed**
- `tests/test_sdk.py`: **11 passed**
- `tests/test_mcp.py`（-m slow）: **8 passed**；余 4 个失败为 `Plan frontmatter is missing`（a764abf6 门禁，预先存在）
- `tests/test_waker_phase1_acceptance.py::test_a3_three_way_audit_join`: **1 passed**
- `tests/test_cli.py` resolve 引导用例: passed

全量 slow 回归（本轮修复后）：**44 failed, 1036 passed, 1 skipped, 6 xfailed**（791s）。对比修复前 60 failed / 1021 passed：减量 16 = 本轮 M58 相关修复数（archive ×8、mcp topic flow ×1、sdk ×5、waker a3 ×1、cli resolve ×1），剩余 44 个经 git worktree HEAD 干净副本复跑确证均为预先存在测试债，M58 相关测试文件（archive/sdk/waker/test_cli）零失败。

M58d 补跑后定向回归：`tests/test_fs_source.py` **20 passed**（含新增 `test_experiment_create_on_fs_topic` 三态断言与 ORM 直插重写的分页测试）。

剩余失败均为预先存在测试债（git worktree HEAD 干净副本复跑确证），与本实验无关：

1. **a764abf6 plan frontmatter 硬门禁 vs 旧裸 content_md**（约 25 个，test_mcp 4 个 + test_todos 聚合 2 个等）
2. **cli.main 缺符号**（ReviewCreate / _ERROR_CODES_SEARCH_FIELDS / _map_sdk_version / review_list 导入错误，约 10 个）
3. **map_sdk evidence.py 导入 server**（跨包依赖，约 3 个）
4. **M54 list 默认 table 格式 vs 旧 yaml 断言**（test_cli_experiment_flow）
5. **DB 数据环境缺话题**（test_18a64691_linkage——DB 中仅 4 个 closed 存量话题）

## 证据（对齐 plan evidence_keys）

1. **pytest 快速门控全量回归通过**：M58 相关失败（archive ×8 / mcp ×1 / sdk ×5 / waker ×1 / cli resolve ×1）全部修复，见上表。
2. **CLI 实测 10 处分支面**（指向 8003 当前代码实例）：create / resolve / archive 无条件 exit 2 引导；advance-round / rollback-round / comment（--storage db）/ close（--storage db）/ reopen 对存量 DB topic（`1ba6ab09-…` fs-refactor-review）exit 2 引导，文案含 `map fs <cmd>` / `topic migrate` / `index.md` / `map/archive/topics/` 等价指引；comment `--file-path` 为合法 slim 参数非残留；`topic --help` 含单轨化提示；fs comment/advance-round/close 路由不回退（M56 三态路由测试全绿佐证）。
3. **server 实测**（uvicorn 起本地工作树实例 :8003，13/13）：8 写端点 + PATCH title → 410 `{"error":"topic_write_retired","hint":…}`；resolve 空体先被 schema validator 422、带合法 body → 410；PATCH `archived` 放行到存在性校验（fake id 404，migrate 归档通道可用）；GET list 200 / detail、comments 路由存活（fake id 404）。
4. **grep 核证**：三 Skill + cli/skills 镜像无 DB 写命令可执行示例残留（退役说明与 slug 自动路由 FS 的等价表述除外）；`experiment-reviewer` 与 `map-project-collab` frontmatter 措辞残留一并修正；map-plugin.yaml version 0.13.0 / requires 0.12。
5. **M58c 结论回写**：PRD v0.13 F2 标注已落地；mention 风险行已决（2026-08-17）。

## 备注

- **运行环境提示**（已更新）：本机常驻 8001/8002 实例已于 M58 落盘后重启加载新代码（health 200 + whoami 验证通过），dev 库已应用 046 迁移（`PRAGMA foreign_key_list(experiments)` 确认 topic FK 退役）。
- **probe 清理**：探测期间在 8001/8002 旧实例上误建 2 个 probe 话题（`defb554e-…` / `8b2a0b9d-…`），已用旧实例 DELETE 软删清理。
- **真实多 agent e2e 补跑（完成）**：`map --persona host e2e run` 九步全 `status=ok` 完整闭环（run log `.map/e2e-logs/20260817T103223Z/run.log`）——FS 话题 `e2e-demo-collaboration-lifecycle`（topic_id `5ceb40d8-…`，12 条评论 R1-R9 + host 三轮）→ host Option B 建实验（v1 `c839507f-…` 因续跑让位 cancel，v2 `34c99441-…` create 时 FS uuid5 不再 404，**M58d 修复实战验证通过**）→ reviewer 评审通过（0 阻塞）→ host 显式 approve（review→approved）→ start --executor host（self-overlap）→ 执行日志 + complete → reviewer accept-result（phase=done）→ 实验 archive + 话题 closed（close note 含 decision / rationale / 3 条 action_items）→ `map work` 无 demo 残留 obligation。补跑期间另修复：① host claude session 损坏（对旧 session 任意 prompt 均 502，new-session 后恢复）；② e2e 驱动 step 6 相位断言错配（见 M58b-3 补跑修正行）。
- **测试债登记**：上节 5 类预先存在失败建议单独开实验清理（frontmatter 门禁波及面最广）。
