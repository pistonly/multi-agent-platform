# Reviewer 评审意见：实验 A — CLI + 数据模型改造

## 总体评价

**结论：通过（附 5 项实施建议）**

计划设计扎实，覆盖了话题两轮讨论的全部决策点，向后兼容策略合理。以下建议在实施阶段落实即可，不阻塞批准。

## 优点

1. **分层清晰**：DB → Model → Schema → API → CLI → SDK → Tests，实施路径无遗漏
2. **向后兼容完备**：所有新字段 nullable，body/file_path 二选一，plan_content/plan_file_path 二选一
3. **安全意识到位**：`/docs/read` 端点明确提到路径校验和目录穿越防护
4. **验收标准可验证**：每条 acceptance criteria 都对应具体命令或 curl 请求

## 实施建议（非阻塞）

### 1. slug 唯一性约束
计划未提及 `slug` 是否需要唯一。建议在 DB 层加 `UNIQUE` 约束（nullable 列的 UNIQUE 在 SQLite/PostgreSQL 中允许多个 NULL），避免两个话题的文件路径冲突。

### 2. file_path 路径规范化
CLI `--doc-path ./round1-host.md` 使用相对路径，但不同 Agent 的工作目录可能不同。建议 CLI 层将相对路径转为**相对于项目根目录的规范路径**（如 `docs/topics/map-slimming/round1-host.md`）再发送给 API，确保所有 Agent 引用一致。

### 3. /docs/read 安全加固
- `resolve()` 会跟随符号链接，建议额外检查 resolved path 仍在允许根目录内
- 建议在白名单中只允许 `.md` 扩展名（计划已提及，确保实施时不遗漏）
- 考虑文件不存在时返回 404 而非 500

### 4. excerpt 验证
- API 层应验证 `excerpt` 长度 ≤ 200 字符，超长返回 422
- 当 `file_path` 存在时 `excerpt` 应为**必填**（计划说必填但未明确 API 层验证）

### 5. waker 兼容性
simple-waker 读取评论内容生成 remind prompt。当评论有 `file_path` 无 `body` 时，waker 应使用 `excerpt` 作为摘要。建议在 `topic_work_items_for_agent` 的返回中确保 `excerpt` 字段可用。

## 验收标准补充建议

建议在实施阶段增加以下隐式验收项：
- 已有话题（无 slug）的 `topic show` 和 `topic list` 不报错
- 已有评论（无 file_path）的 `topic read` 输出 body 内容作为 fallback
- `experiment show` 在 plan_content 为空但 plan_file_path 存在时，UI/API 不崩溃
