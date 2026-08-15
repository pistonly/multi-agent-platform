---
evidence:
  allow_missing_evidence: true
---

# 实验 A：CLI + 数据模型改造（MAP 瘦身）— 实施日志

## 概要

完成了 MAP 平台瘦身的全部数据模型、Schema、Service 层、CLI 和 API 端点改造。平台现在支持以本地 MD 文件路径替代内联内容存储，为 UI 渲染本地 MD 文件奠定了基础。

## 实施 log

### 1. 数据库迁移（Alembic 044）

- 文件：`alembic/versions/044_doc_ref_slimming.py`
- 新增字段：
  - `topics.slug` (String(256), nullable, 带部分唯一索引)
  - `topic_comments.file_path` (Text, nullable)
  - `topic_comments.excerpt` (String(200), nullable)
  - `experiments.plan_file_path` (Text, nullable)
  - `experiments.log_file_path` (Text, nullable)
- 迁移使用 idempotent 模式（检查列/索引是否存在再添加），支持 SQLite 和 PostgreSQL

### 2. ORM 模型更新

- 文件：`server/domain/models.py`
- Topic 模型：添加 `slug` 字段
- TopicComment 模型：添加 `file_path` 和 `excerpt` 字段
- Experiment 模型：添加 `plan_file_path` 和 `log_file_path` 字段

### 3. Pydantic Schema 更新

- 文件：`sdk/python/map_types/schemas.py`
- `TopicCreate`：添加 `slug` 字段
- `TopicCommentCreate`：添加 `file_path` 和 `excerpt` 字段，`body` 改为可选
- `TopicCommentRead`：添加 `file_path` 和 `excerpt` 字段
- `TopicSummaryRead`：添加 `slug` 字段
- `ExperimentCreate`：添加 `plan_file_path` 字段
- `ExperimentComplete`：添加 `log_file_path` 字段，`content_md` 改为可选，添加 validator 确保至少提供 `content_md` 或 `log_file_path`
- `ExperimentSummaryRead`：添加 `plan_file_path` 和 `log_file_path` 字段

### 4. Service 层更新

- `server/services/topic_lifecycle_service.py`：
  - `create_topic`：传递 `slug=payload.slug` 到 Topic 构造器
  - `topic_summaries_for_topics`：传递 `slug=topic.slug` 到 TopicSummaryRead
- `server/services/topic_comment_service.py`：
  - `create_topic_comment`：传递 `file_path` 和 `excerpt`，当 body 为 None 时生成 stub
  - `topic_comment_read`：传递 `file_path` 和 `excerpt` 到响应
  - `list_topic_comments`：传递 `file_path` 和 `excerpt` 到响应
  - `_build_comment_tree`：传递 `file_path` 和 `excerpt` 到树节点
- `server/services/project_service.py`：
  - `create_experiment`：传递 `plan_file_path=payload.plan_file_path`
- `server/services/phase_service.py`：
  - `complete_experiment`：设置 `experiment.log_file_path`，使用 `log_content` 替代直接引用 `payload.content_md`（处理 None 情况）

### 5. CLI 命令更新

- `cli/commands/topic.py`：
  - `topic create`：添加 `--slug` 选项
  - `topic comment`：添加 `--file-path` 和 `--excerpt` 选项，支持三选一输入模式（--body / --file / --file-path）
- `cli/commands/experiment.py`：
  - `experiment create`：添加 `--plan-file-path` 选项，与 `--plan-file` 二选一
  - `experiment complete`：添加 `--log-file-path` 选项，与 `--file` 二选一

### 6. 新增 docs/read API 端点

- 文件：`server/api/docs.py`（新建）
- 端点：`GET /api/v1/projects/{project_id}/docs/read?path=<relative_path>`
- 功能：读取项目工作空间内的本地 MD 文件内容
- 安全措施：
  - 路径遍历保护（resolve 后验证在 workspace 内）
  - 文件扩展名白名单（.md, .markdown, .txt）
  - 文件大小限制（2MB）
  - 需要项目访问权限验证
- 在 `server/api/router.py` 和 `server/main.py` 中注册路由

## 风险

- **向后兼容**：所有新字段均为 nullable/optional，现有 API 调用无需修改。已通过 195 个测试验证。
- **路径安全**：docs/read 端点实现了路径遍历保护，但需要在生产环境中验证 workspace_path 配置正确。
- **文件一致性**：平台不管理本地 MD 文件的生命周期（创建/删除），仅记录路径。文件可能被外部修改或删除，调用方需处理文件不存在的情况。
- **CLI 兼容性**：`--plan-file` 和 `--file` 参数从 required 改为 optional，不影响现有脚本（仍可使用）。

## acceptance

- [x] 数据库迁移成功执行（044）
- [x] `topic create --slug` 可用，slug 在列表和详情中返回
- [x] `topic comment --file-path --excerpt` 可用，file_path 和 excerpt 在详情中返回
- [x] `experiment create --plan-file-path` 可用（作为 --plan-file 的替代）
- [x] `experiment complete --log-file-path` 可用（作为 --file 的替代）
- [x] `GET /docs/read` 端点正常读取本地 MD 文件
- [x] 路径遍历保护生效（../../../etc/passwd 被拒绝）
- [x] 现有测试 195 passed, 2 pre-existing failures（非本次改动引起）
