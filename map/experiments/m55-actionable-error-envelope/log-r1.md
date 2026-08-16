# M55 log

## Round 1：M55A–F 实施与验证（2026-08-16）

### 交付（M55A–F 六子项）

- **M55A（422 可执行化）**：`server/main.py` 新增 `register_request_validation_handler`——`RequestValidationError` 保留 FastAPI 默认 `detail` 数组（字节兼容），附加 `error_code=request_validation_error` / `retryable=false` / `hint`（缺失字段清单 + experiments 端点最小 payload 示例），字段名与 `StateTransitionError` 信封一致
- **M55B（frontmatter 模板提示）**：`plan_marker_service._frontmatter_template_hint`——hint 附 ~10 行 YAML fenced 模板（4 必填字段示例 + `dependencies: []` 注释），缺失字段在前缀标注
- **M55C（dependencies 空列表）**：`_collect_warnings` 显式空列表合法（无依赖不再写 `- none` 哨兵）；缺失 key 仍拦截；其余三字段维持非空
- **M55D（E8 瘦身门禁）**：server 侧 `create_experiment` 仅对 `content_md` 非空的形态执行内容门禁；CLI 侧本地预检扩展到 `--plan-file-path`（同一 lint，`--force-lint-bypass` 语义不变）——r1 评审要求的「无门禁空洞」配对闭环
- **M55E（CLI 错误渲染）**：`cli/main.py` hint / docs / recover 分行渲染，多行 hint（模板）原样透传；`--format json` 错误信封结构不变（M54C 冻结契约）
- **M55F（日志纪律）**：`experiment-host/SKILL.md` 新增条目——create/revise/submit 失败重试成功必须补 `experiment log` 记录 422 原文与修复动作

### 测试证据

- `tests/test_m55_error_envelope.py` 新增 16 用例：**16 passed**（API 级 TestClient + CLI 级 stub transport；已入 fast-gate 白名单）
- 快速门控全量：**455 passed / 2 skipped**（基线 437+16，无回归）
- 实验域全量（experiments / plan_marker / plan_revise / plan_validate_cli / auth_experiment_access / experiment_executor / topics / rebutted_e2e，含 slow）：**109 passed / 0 failed**
- `ruff check server/ cli/ tests/ sdk/`：**All checks passed**

### 真机（wire）验证

- **E8 slim**：`experiment create --plan-file-path`（瘦身形态，frontmatter 含 `dependencies: []`）→ **201**；DB 落库 `plan_file_path` + PlanVersion v1 stub；`experiment show` 回读 `plan_file_path` / stub / `mode` 全部正确
- **M55B/E**：坏计划 + `--force-lint-bypass` → 服务端 422 `STATE_MACHINE_PLAN_MARKER_MISSING`，stderr 完整渲染模板 hint（可直接复制修复），exit=1
- 对照组：同一命令打到**旧代码 server（8001）**复现 E8 原始 bug（`Plan frontmatter is missing`）——修复前后行为差异实证

### wire 阶段新发现与修复（按 M55F 纪律记录）

1. **M55A 示例永不匹配 bug**：示例表按精确路径 `/api/v1/experiments` 匹配，而真实路由是 `/api/v1/projects/{uuid}/experiments`——wire 首轮 422 无示例暴露此 bug，改为 (method, 路径后缀) 匹配（测试随之转绿）
2. **「409 误报」根因**（r0 遗留的测试失败）：并非 topic 冲突，而是 `plan_versions.content_md NOT NULL`——瘦身形态 content_md=None 插入即崩，`except IntegrityError` 兜底把它误标成 "Topic already has an active experiment"。双修复：a) 瘦身创建写入自述 stub（`<!-- slim create: plan content lives in <path> -->`，符合 ExperimentCreate 注释「content_md may be a stub」契约）；b) IntegrityError 分支只在命中 `uq_experiment_one_active_per_topic` 索引时报 topic 冲突，其余如实上报约束原文
3. **detail 读路径漏字段**：`get_experiment_detail` 手工构造漏传 `plan_file_path` / `log_file_path` / `mode`（summary 路径走 model_validate 无此问题）——wire 回读 None 暴露，补齐
4. **`tests/_frontmatter.py` 空列表序列化**：`make_valid_plan(dependencies=[])` 生成裸 `dependencies:`（YAML 解析为 None → MISSING_FIELD），改为内联 `key: []`，docstring 同步 M55C 语义
5. **存量失败顺手修复**：`test_plan_revise_no_archive_when_content_unchanged` 在 HEAD 上即失败（frontmatter 迁移漏改 fixture：v1 存裸文本、revise payload 带 frontmatter，字符串不等误触版本 bump）——已按 helper 约定修正，6/6 绿

### wire 验证事故与处置（M55F 条目实证）

- 起临时 server 验证时误用 `DATABASE_URL` 环境变量；`Settings` 前缀为 `MAP_`，未生效 → 临时实例落到真实 `data/map.db`，创建了 wire 测试实验 `1db109ca`（draft）
- 处置：经官方 SDK（`MAPClient`，host persona）调 `POST /experiments/{id}/cancel` → **200 cancelled**（CLI 未封装 cancel 命令，SDK 为合规路径；留完整审计记录）；随后以 `MAP_DATABASE_URL` + DB 副本重做隔离验证，零真实库污染
- 教训入库：本条即 M55F「失败重试必须记日志」的首个执行样例

### 范围外移交

- `map experiment cancel` CLI 命令缺失（端点存在，SDK 可调）——后续里程碑评估
- FS 话题（uuid5）不能被 `experiment.topic_id` 引用（r0 已记录，维持移交）
