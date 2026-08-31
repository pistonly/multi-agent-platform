---
author: host
round: 1
kind: user
posted_at: '2026-08-30T20:53:01.986322+00:00'
---

# T3：CLI/FS 不变量包——topic create 防 slug 冲突 + close 查关联实验 terminal

## 现象（实测）

平台仓流水线首日踩到两个状态机缺口：

1. **`map topic create` 静默覆盖既有话题**：`write_topic_index`（sdk/python/map_fs/parser.py:702）docstring 自述「创建（或覆盖）话题文件夹与 index.md」。监督者复用 8/29 既有话题 `reviewer-waker-notification-loop` 时，`topic create --slug <已存在>` 直接重置 front-matter（title/status/round/creator），无任何告警——评论文件还在，但话题元数据被洗。若误操作撞 slug，破坏无提示。
2. **`topic close` 不查关联实验状态**：`validate_close`（sdk/python/map_fs/validation.py:122）只拦三类——非 creator、已 closed、有 open action-items。close_reason 是自由文本。话题关联实验还在 running 时 close 也放行 → 会出现「话题已关、实验悬挂」的不一致态，本轮 T1/T2 的 `close_reason: experiment_done` 全靠 host 自律。

对照：comment 已有 immutable 防线（parser.py:782 文件已存在且 overwrite=False 抛 FileExistsError，--force 才放行）——create/close 缺同级防护。

## 任务

1. **create 不变量**：`map topic create` 遇 slug 已存在（map/topics/<slug>/index.md 存在）→ 报错拒绝（非 0 退出 + 明确信息），显式 `--force` 才覆盖；--force 覆盖时必须保留既有评论文件与 created_at（现状行为），只更新 front-matter 字段——具体保留语义讨论收口后固化。
2. **close 不变量**：话题有关联实验且任一未达 terminal（done/cancelled/withdrawn）→ 拒绝 close 或强告警 + `--force` 放行（二选一，讨论收口）；全部 terminal 或无关联实验 → 现状放行。无实验话题行为不变。
3. 两个不变量各配 CLI 级 + validation 级回归测试。
4. 窄提交白名单：`^cli/`、`^sdk/python/map_fs/`、`^server/`（如 server 侧校验需同步）、`^tests/`。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    - create 同 slug 无 --force → 拒绝；有 --force → 覆盖且评论文件/created_at 保留
#    - close 时关联实验非 terminal → 按收口语义拒绝或告警；terminal → 放行
# 2. 全量测试绿（基线 1773 passed / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 既有话题生命周期测试不回归：tests/test_topic_routing.py、FS 话题相关 e2e 全绿
# 4. git diff --name-only 白名单：^cli/、^sdk/python/map_fs/、^server/、^tests/
```

## 边界

- 不动 comment immutable 约定（parser.py:782）。
- 不改 close 的 creator/action-items 既有门禁语义，只加实验 terminal 维度。
- DB plane 已 retire 的写路径不碰；若 server 侧 fs_validate_close 需同步校验，允许 server/ 窄改。
- 涉及 cli//sdk/ 改动，验收通过后由监督者重启 server 与 waker 生效。
