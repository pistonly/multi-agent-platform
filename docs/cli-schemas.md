# CLI Structured File Schemas

> 集中列出 `map` CLI 接受的 YAML 配置文件 schema。每个 schema 都给出：
> 1. 来源 SDK 文件（权威）
> 2. CLI 入口（`--schema` 标志可直接打印模板）
> 3. 必填/可选字段清单

## 怎么用

任何一个接受结构化 YAML 文件的 CLI 命令，现在都支持 `--schema` 标志，
打印可复制粘贴的模板：

```bash
map --persona host experiment accept-result --schema
map --persona host experiment complete --schema
map --persona reviewer experiment accept-result --schema
```

出错时，错误信息末尾会附 schema 定位指针。

---

## review-verdict-file

**用途**：`map experiment accept-result --review-verdict-file <yaml>` /
`map experiment reject-result --review-verdict-file <yaml>`

**SDK 权威源**：`sdk/python/map_types/schemas/experiment.py:ReviewVerdictFile`

**模板**（`--schema` 输出）：

```yaml
review_id: 00000000-0000-0000-0000-000000000000  # <-- replace
verdicts:
  - item_id: 00000000-0000-0000-0000-000000000000  # <-- review item UUID
    verdict: passed  # passed | failed | waived
    # reason: REQUIRED only when verdict == waived (50-1000 chars)
invariants:
  - item_id: 00000000-0000-0000-0000-000000000000  # <-- replace
    verified: true
    # note: optional, max 1000 chars
```

**字段约束**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `review_id` | UUID | 是 | 从 `map experiment review list --id <exp-id>` 取得 |
| `verdicts` | list | 是（可空）| 每个 review item 一条 verdict；不允许重复 item_id |
| `verdicts[].item_id` | UUID | 是 | Review item UUID |
| `verdicts[].verdict` | enum | 是 | `passed` / `failed` / `waived` |
| `verdicts[].reason` | str | 当 verdict==waived | 50-1000 chars 的豁免说明 |
| `invariants` | list | 否 | 验证项（item_id, verified, note）|

**别名**（cli-ux PR3）：`accept` / `reject` / `dismiss` 也接受，分别映射到
`passed` / `failed` / `waived`。序列化输出仍是规范值。

---

## experiment-complete-metadata

**用途**：`map experiment complete --metadata <yaml>`

**SDK 权威源**：`cli/main.py:_load_complete_metadata`（CLI 端校验；
server 端在 `server/services/phase_service.py` 做扩展校验）

**模板**（`--schema` 输出）：

```yaml
api_health: ok
alembic_current:
  head: "<revision>"
  upgrade_clean: true
pytest_summary:
  total: 0
  passed: 0
  failed: 0
  skipped: 0
smoke:
  api_health: ok
  notes: "..."
```

**接受键**（至少一个；否则需 `--allow-missing-evidence`）：

- `api_health` / `health`：API 容器健康检查结果
- `alembic_current`：当前 alembic revision（upgrade 是否干净）
- `pytest_summary` / `test_summary`：本地 pytest 结果
- `smoke` / `smoke_result`：部署/容器内冒烟测试
- `image_digest`：发布镜像 digest
- `acceptance`：手写 acceptance 列表

完整键集见 `cli/main.py::EVIDENCE_METADATA_KEYS`。

---

## plan frontmatter

**用途**：`map experiment create --plan-file <md>` / `--force-lint-bypass`

**SDK 权威源**：`server/services/plan_marker_service.py`

**必填字段**（缺一即 lint fail）：

- `title`：实验标题
- `acceptance`：验收清单（list of strings）
- `evidence_keys`：验收证据路径（list of strings）
- `dependencies`：前置实验 / 约束（list of strings）；**v0.12 起无依赖写 `dependencies: []`**（显式空列表合法，不再需要 `- none` 哨兵值；其余三个字段仍要求非空）

**示例**：

```yaml
---
title: cli-ux follow-up — schema 发现 + notification 批量过滤 + verdict 别名
acceptance:
  - PR1 schema 子命令：accept-result 与 complete 支持 --schema 标志
  - 全程 fast-gate 绿
evidence_keys:
  - tests/test_cli_schema_discovery.py
dependencies: []
---
```

---

## 维护说明

- schema 变更时同步更新本文件 + CLI 模板常量（`_REVIEW_VERDICT_SCHEMA_YAML`、
  `_COMPLETE_METADATA_SCHEMA_YAML`）。
- `--schema` 输出与 SDK 模型走 round-trip 测试（见
  `tests/test_cli_schema_discovery.py`），避免 SDK 改完 CLI 模板漂移。
