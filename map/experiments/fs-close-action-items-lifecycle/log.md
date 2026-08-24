# 实验日志：FS close_note 的 action_items 解析入跟踪（plan v3 实施记录）

**执行时间**: 2026-08-24
**执行者**: multi-agent-platform-host
**结果**: v3 全量实施完成。新增 20 单测（A1–A7 对应）+ 11 文件合并回归 150 passed / 0 failed；live mini fixture 全链实测输出落地；grep 核证三处。待 reviewer result_review（host 不自评）。

> plan v3 变更来源（用户对话定案）：action_items 收敛时结构化落盘 `action-items.yaml`（FS 事实源），close 门禁升级为唯一防线，closed = 零尾款；原 v2「close 时解析 close_note → DB 建行 + alembic 051」路线退役（见 log-v3-revision.md）。

## I1 yaml schema + parser（A1）

| 文件 | 改动 |
|------|------|
| `sdk/python/map_fs/parser.py` | `_ACTION_ITEMS_FILE`；`FsActionItem`（id/title/owner/status open\|done\|cancelled/evidence/reason/created_at）；`parse_action_items_file`（缺文件/空 → 空列表无错；非列表/逐项校验 → (空, error) 不静默）；`parse_topic_dir` 挂入 `FsTopic.action_items/action_items_error`；`read/write_action_items`（_atomic_write + safe_dump） |
| `sdk/python/map_fs/__init__.py` | 导出 FsActionItem / parse / read / write |
| `sdk/python/map_types/schemas/fs.py` | `FsActionItemRead`；`FsTopicDetailRead.action_items/action_items_error`；`_canonical_topic_dict` 内容 hash 纳入 action_items（投影缓存稳定性） |

## I2 投影义务（A2/A7）

| 文件 | 改动 |
|------|------|
| `server/services/fs_source_service.py` | `_TopicView` 挂 action_items/error；三视角转换器（fs/projection/as_fs_topic）同源 marshal；`fs_topic_progress_for_agent` 把 open 项投影为 `kind=action_items` obligation（`idempotency_key=fs:action_item:<slug>:<id>`，owner persona 精确路由，他人不可见；stale nudge 仅当无非派生义务且无 open 项才发）；projection-cache 回退分支同源（A7） |
| `server/api/fs.py` | 无 —— 投影经既有 `/work` 通道，不新增端点 |

## I3 close 门禁（A3，唯一防线）

| 文件 | 改动 |
|------|------|
| `server/services/fs_source_service.py` | `FsOpenActionItemsError`；`validate_fs_close` 在 closed 检查后：yaml 格式错漏 → 409；存在 `status: open` 项 → 409 带各项 id/title/owner；全 done/cancelled 放行。invariant：closed = 零尾款 |
| `server/api/fs.py` | `_validate_error_http` 映射 409 `action_items_open`；直接 `/close` 端点补 catch 该错误（原 500 风险） |

## I4 CLI complete/cancel/add（A4/A5）

| 文件 | 改动 |
|------|------|
| `cli/commands/topic.py` | `topic action-item` typer 组：`add`（max+1）/ `complete`（空 evidence → exit 2 拒绝，仅 open→done）/ `cancel`（open→cancelled + reason）/ `list`；纯本地写回（read → mutate → `write_action_items` → maybe_auto_sync）；格式错漏时拒绝写回（不覆盖手写内容） |
| `cli/commands/fs.py` | `_render_ack_error` → `_render_validate_error`（兼容 round_ack_pending + action_items_open）；**修复 live 传输形态**：`map_client.client.py:183` 将 error body 的 detail `str()` 成 repr 字符串，`_render_validate_error` 原先按物化 dict 分支而 live 静默降级为裸 409 —— 现用 json/ast 还原字符串形态再分支（顺带修复既有 round_ack_pending live 降级，同根因） |
| `tests/cli/test_fs_persona.py` | 渲染函数重命名引用同步（2 处） |

## I5 Skill 文档（A6）

| 文件 | 改动 |
|------|------|
| `.cursor/skills/topic-host/references/host-checklist.md` | close_note 模板去掉 action_items 文本段；新增 §3b「执行项(轻量)落 action-items.yaml，不塞 close_note（plan v3 D1）」：时序（收敛时）、CLI 命令、门禁校验、约定废弃 |
| `.cursor/skills/topic-host/SKILL.md`、`map-project-collab/SKILL.md`、`references/wake.md`、`references/commands.md`、`experiment-gate-rubric.md` | kind=action_items 待办语义 / wake kind 行 / `map topic action-item` 命令速查同步 |

## I6 验证

### pytest_summary（新增单测 A1–A7 全绿 + 既有套件无回归）

`tests/test_fs_action_items.py`（新增 20 用例）：yaml 解析 roundtrip/缺文件/手写无 id 兜底/六种格式错漏不静默；close 门禁 409 与清零放行 / cancel 不拦 / yaml 损坏拦；owner 精确路由（A2，done 不投影）；投影缓存回退（A7，push + remote close validate 409→放行，base_revision）；CLI add/complete/cancel/空 evidence 拒/损坏 yaml 拒写/渲染（含字符串形态 detail）。

合并回归（11 文件，含 fs_source / remote / routing / projection / archive / todos / simple_waker / topic_work_items / persona）：

```
150 passed, 32 deselected, 3 warnings in 69.97s
```

`ruff check` 对全部改动文件：通过。

### 实测输出（本实验 mini fixture 全链，throwaway server 18401 + 独立 sqlite，不惊动共享 18400 daemon）

收敛落盘 → owner 义务 → 证据清零 → close 放行，全链走通：

```text
$ map --persona host topic action-item add --topic e2e-ai-gate --owner participant --title "合并主分支"
Wrote …/action-items.yaml — 新增 #1 [participant] 合并主分支

$ map --persona participant work          # → topic_progress 出现：
      idempotency_key: fs:action_item:e2e-ai-gate:1
      clear_action: complete_or_cancel_action_item
      excerpt: '行动项 #1: 合并主分支'
$ map --persona host work                 # →
      idempotency_key: fs:action_item:e2e-ai-gate:2
      excerpt: '行动项 #2: 同步发布文档'

$ map --persona host topic close --id e2e-ai-gate --reason no_experiment_needed --note "x"
Error 409: action items 未清零 — 无法关闭（closed = 零尾款）
  - #1 待清零项 (owner: participant) — 用 `map topic action-item complete/cancel` 清零后再 close
  # 其余 open 项逐条列出；仅 409，话题未关闭

$ map --persona participant topic action-item complete --topic e2e-ai-gate --id 1 --evidence "6aaca4c (e2e)"
Wrote …/action-items.yaml — #1 → done（evidence: …）
$ map --persona host topic action-item complete --topic e2e-ai-gate --id 2 --evidence "docs/api.md (e2e)"
Wrote …/action-items.yaml — #2 → done（evidence: …）
$ map --persona host topic action-item complete --topic e2e-ai-gate --id 1 --evidence "  "
Error: --evidence 必填（commit hash / pytest 摘要 / 文件路径），空证据拒绝

$ map --persona participant work          # action_items 义务消失（清零）
$ map --persona host topic close --id e2e-ai-gate --reason no_experiment_needed --note "全链演示完成"
# ok → index.md status: closed
```

### grep 核证

- `validate_fs_close` 含 open 项 409 分支（`FsOpenActionItemsError` → `action_items_open`）
- `fs_topic_progress_for_agent` 含 action_items 投影（`kind="action_items"` / `clear_action="complete_or_cancel_action_item"`）
- `host-checklist.md` 已改引导收敛时落盘（§3b，close_note 模板移除 action_items 文本段）

## 备注

- 实测输出用 throwaway server（`MAP_PORT=18401` + `/tmp/map-e2e-ai.db`）+ bootstrap 临时 workspace，跑完已 kill 并清理 tmp；共享 18400 daemon 全程未动。
- live 渲染修复见 I4：`map_client` 统一把 error detail `str()`（client.py:183），结构化 409 的 dict 以 Python repr 字符串到达 CLI，`_render_validate_error` 原判断失效。该问题同样压着既有 round_ack_pending 的 live 渲染，本次同根因一并修掉。
- plan v3 明确零 schema 迁移、零 DB 建行（D4/D6）：本次无 alembic 改动，`topic_action_items` 表未被触碰。
