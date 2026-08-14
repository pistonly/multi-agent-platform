---
title: "实验 A：CLI + 数据模型改造（MAP 瘦身）"
acceptance:
  - "topic create --slug map-slimming 成功创建带 slug 的话题"
  - "topic comment --doc-path ./test.md --excerpt '测试' 成功创建评论，平台只存路径和摘要"
  - "topic read --id <uuid> --round 1 输出该轮所有 MD 文件内容"
  - "GET /api/v1/docs/read?path=docs/topics/test/test.md 返回文件内容 + ETag"
  - "路径穿越攻击被拒绝（../../../etc/passwd 返回 403）"
  - "已有的 topic comment --body '...' 用法仍然有效（向后兼容）"
  - "experiment create --plan-file ./plan.md 存路径不存内容"
  - "所有现有测试通过"
evidence_keys:
  - "alembic 迁移 044 成功执行（sqlite3 PRAGMA table_info 验证新列存在）"
  - "map topic comment --doc-path 命令成功执行且 API 返回 file_path"
  - "curl /api/v1/docs/read 返回 200 + 文件内容"
  - "pytest 测试全绿"
dependencies:
  - "none — 本实验为 MAP 瘦身方案的第一个实验，无前置依赖"
---

# 实验 A：CLI + 数据模型改造

## 目标

将 MAP 平台的评论和实验内容从数据库存储改为本地 MD 文件引用，平台只保留元数据和文件路径索引。

## 背景

话题 `694ed1c9-3740-4e14-9f9c-681a8d844404` 两轮讨论已收敛，本实验实施 round1 和 round2 中确定的数据模型与 CLI 改造方案。

## 改动范围

### 1. 数据库 Schema 迁移（Alembic 044）

**topics 表**：新增 `slug` 列
- `slug TEXT` — 用于文件路径约定（如 `map-slimming`），nullable，兼容已有数据

**comments 表**：新增 `file_path` 和 `excerpt` 列
- `file_path TEXT` — 本地 MD 文件的相对路径（如 `docs/topics/map-slimming/round1-host.md`），nullable，兼容已有 body 模式
- `excerpt TEXT` — 一句话摘要（< 200 字符），用于列表展示和通知

**experiments 表**：`plan_content` 改为可空，新增 `plan_file_path`
- `plan_file_path TEXT` — 实验计划 MD 文件路径，nullable
- `plan_content` 保留为可空字段，向后兼容（有 file_path 时不存 content）
- `log_file_path TEXT` — 实验日志 MD 文件路径，nullable（同理）

### 2. API 改造

**POST /api/v1/topics** — 支持 `slug` 参数
- 请求体新增可选 `slug` 字段
- 若未提供，从标题自动生成（slugify）

**POST /api/v1/topics/{id}/comments** — 支持 `file_path` + `excerpt`
- 请求体新增可选 `file_path` 和 `excerpt` 字段
- 当 `file_path` 存在时，`body` 可为空（平台只存路径和摘要）
- 当 `file_path` 不存在时，保持现有行为（存 body），向后兼容

**POST /api/v1/experiments** — `plan_file` 改为存路径
- 当传入 `plan_file` 时，API 只读取文件路径并存入 `plan_file_path`，不读取文件内容入库
- `plan_content` 保留为可选，兼容直接传内容的方式

**POST /api/v1/experiments/{id}/complete** — `file` 改为存路径
- 同上，`log_file_path` 存路径，不存内容

**新增 GET /api/v1/docs/read**
- 查询参数 `path` — 相对路径（如 `docs/topics/map-slimming/round1-host.md`）
- 路径校验：只允许 `docs/topics/` 和 `docs/experiments/` 下的 `.md` 文件
- 返回文件内容 + `ETag` 响应头（基于 mtime + size）
- 防目录穿越：resolve 后检查路径是否在允许的根目录内

### 3. CLI 改造

**`topic create`** — 新增 `--slug` 参数
- `map --persona host topic create --title "..." --slug map-slimming`
- 未指定时从标题自动生成

**`topic comment`** — 新增 `--doc-path` 和 `--excerpt` 参数
- `map --persona host topic comment --id <uuid> --doc-path ./round1-host.md --excerpt "摘要"`
- `--doc-path` 与 `--body` 互斥（二选一）
- CLI 层做 try-with-cleanup：先写文件成功再调 API，API 失败则报错（不删文件，让用户决定）

**`topic read`** — 新增命令
- `map --persona host topic read --id <uuid> --round 1`
- 聚合输出该轮所有参与者的 MD 文件内容，方便 Agent 一次性读取上下文

**`experiment create`** — `--plan-file` 改为存路径
- 现有 `--plan-file` 参数保留，但行为改为：API 存路径不存内容

**`experiment complete`** — `--file` 改为存路径
- 同上

### 4. 向后兼容

- 所有新增字段均为 nullable，已有数据不受影响
- `body` 字段保留，`file_path` 和 `body` 二选一
- `plan_content` 保留，`plan_file_path` 和 `plan_content` 二选一
- 已有的 `topic comment --body "..."` 用法继续有效

## 实施步骤

1. 创建 Alembic 迁移 044（新增 slug, file_path, excerpt, plan_file_path, log_file_path 列）
2. 更新 SQLAlchemy 模型（Topic, Comment, Experiment）
3. 更新 Pydantic schemas（TopicCreate, CommentCreate, ExperimentCreate 等）
4. 更新 API endpoints（topics, comments, experiments, docs/read）
5. 更新 CLI commands（topic create/comment/read, experiment create/complete）
6. 更新 SDK types（map_types/schemas.py）
7. 编写测试覆盖新功能
8. 验证向后兼容（已有数据不报错）

## 验证标准

- [x] 新建 topic 时可指定 `--slug`
- [ ] `topic comment --doc-path ./test.md --excerpt "测试"` 成功创建评论，平台只存路径
- [ ] `topic read --id <uuid> --round 1` 输出该轮所有 MD 文件内容
- [ ] `GET /api/v1/docs/read?path=docs/topics/test/test.md` 返回文件内容 + ETag
- [ ] 路径穿越攻击被拒绝（`../../../etc/passwd` 返回 403）
- [ ] 已有的 `topic comment --body "..."` 用法仍然有效
- [ ] `experiment create --plan-file ./plan.md` 存路径不存内容
- [ ] 所有现有测试通过
