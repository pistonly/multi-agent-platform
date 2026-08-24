# FS close_note 的 action_items 解析入跟踪 — 结果审批（accept，复审）

## 结论

**通过（复审）**。host 已补收口 commit `6889869`（HEAD，19 文件 1292 行，覆盖上次驳回列出的全部代码/Skill/测试文件），工作区干净（0 未提交），HEAD 与工作区 7 个关键文件 hash 完全一致。上次驳回的唯一理由（未收口）已消除；v3 plan（用户对话定案：action_items 收敛落盘 yaml + FS 投影义务 + close 零尾款门禁）A1-A7 实现核证成立。

## 驳回→补收口 复原核对

| 项 | 上次驳回状态 | 本次复审 |
|----|-------------|----------|
| 收口 commit | 缺位（HEAD=a939d7f 仅文档） | ✅ `6889869` 为 HEAD/祖先，`feat(fs): action-items.yaml 生命周期全链` |
| 覆盖面 | 13 功能文件 + Skill + 新测试全 M/?? | ✅ 7 代码文件（fs.py/topic.py/parser.py/__init__/schemas/fs.py/api/fs.py/fs_source_service.py）+ 5 Skill 文档 + tests/test_fs_action_items.py + 实验文档 |
| 工作区残余 | 全部未提交 | ✅ 0 未提交（git status 干净） |
| HEAD↔工作区一致性 | — | ✅ 7 关键文件 hash 逐一对齐（无收口后漂移） |

## 验收逐条核验（对照 HEAD `6889869`）

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 yaml 事实源 + parser | ✅ | HEAD `sdk/python/map_fs/parser.py`：`_ACTION_ITEMS_FILE`/`parse_action_items_file`(357)/`read_action_items`(706)/`write_action_items`(718, `_atomic_write`+safe_dump)/`FsTopic.action_items`(499)；`__init__` 导出；schemas `FsActionItemRead` + `_canonical_topic_dict` 内容 hash 纳入（投影缓存稳定） |
| A2 投影义务 | ✅ | HEAD `fs_source_service.py:1228` `kind="action_items"`、`idempotency_key="fs:action_item:{slug}:{id}"`(1236)、`clear_action`(1237)；owner persona 精确路由（他人不可见）；stale nudge 仅当无 open 项才发；projection 回退分支同源(A7) |
| A3 close 门禁 | ✅ | `FsOpenActionItemsError`(103)、`validate_fs_close` open 项 409(1602-1605, 带各项 id/title/owner)；`api/fs.py:301-305` `action_items_open` + 直接 /close catch(636)；closed=零尾款 |
| A4 complete 带证据 | ✅ | `cli/commands/topic.py` `action-item complete --evidence`（空证据 exit 2 拒）；add/cancel/list；纯本地写 + maybe_auto_sync |
| A5 cancel 语义 | ✅ | cancel → status=cancelled + reason，不挡 close |
| A6 Skill 文档 | ✅ | host-checklist.md §3b（收敛时落盘 + close_note 模板移除 action_items 段）；wake.md kind 表加 action_items；topic-host/map-project-collab SKILL + gate-rubric 同步 |
| A7 投影缓存兼容 | ✅ | 三视角转换器 fs/projection/as_fs_topic 均 marshal action_items；`test_049_project_fs_content_config` 同步 |
| 测试面 | ✅ | **复跑**：test_fs_action_items.py 20 + test_fs_persona 5 = 25 passed；合并回归（fs_source/projection/remote/archive/todos/simple_waker/topic_work_items/persona 等）**281 passed**，唯一 2 failed 为预存债务（见下） |

## 合并回归说明

- 281 passed / 2 failed：2 failed 为 `test_review_list_archived_filter`（引用不存在的 `cli.main.review_list`）——本次收口 commit `6889869` 未触碰 cli/main.py 或该测试，HEAD cli/main.py 亦无 review_list（grep 0），与 4e45f4e 审批时确认的既存债务同源，**与本实验零相关**。
- live 渲染修复附带（I4）：`_render_validate_error` 兼容 `map_client` 把 error detail `str()` 成 repr 字符串的形态，顺带修复既有 round_ack_pending live 降级——同根因修复，属合理附带，不属范围越界。

## 遗留（非阻塞）

- `test_review_list_archived_filter` 2 挂为预存债务（cli.main 缺 review_list），可归独立修复（与本实验无关，已在 4e45f4e 审批中标注）。
- A6 约定 close_note 中 action_items 文本段废弃，存量无回填——语义正确（closed=零尾款 invariant），文档已拦截新写法。
