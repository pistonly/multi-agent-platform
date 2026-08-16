# M55 错误信封可执行化 · 实验结果（规范模板版）

**实验**: `m55-actionable-error-envelope`（`84cccb2e-2a69-4339-9dfd-c3df1bf23d31`）
**周期**: 2026-08-16（Round 0–1）
**结论**: 全部 7 条 acceptance 达成，v0.12 错误信封六子项（A–F）交付完毕，E6/E8 两个评审缺口收敛

## summary

M55 六子项全部落地：**M55A** 422 统一 handler——`RequestValidationError` 保留默认 `detail` 数组并附 `error_code` / `hint`（缺失字段清单 + experiments 端点最小 payload 示例）/ `retryable`，与 `StateTransitionError` 信封同构；**M55B** frontmatter 失败 hint 附 ~10 行可复制 YAML 模板（缺失字段前缀标注）；**M55C** `dependencies: []` 显式空列表合法，`- none` 哨兵值淘汰（其余三字段维持非空）；**M55D** E8 双侧配对——server 仅对含 `content_md` 形态执行内容门禁 + 瘦身形态写入自述 stub，CLI 本地预检扩展到 `--plan-file-path`（无门禁空洞）；**M55E** CLI hint/docs/recover 分行渲染、多行模板原样透传，JSON 错误信封不变（M54C 冻结契约）；**M55F** experiment-host Skill 日志纪律条目。测试 **16 新增全绿 + 快速门控 455 passed / 2 skipped + 实验域全量 109 passed**，ruff 清零；wire 验证 E8 slim 201 / 422 模板渲染 / 新旧 server 行为对照全部通过。wire 阶段额外修复三处真实缺陷：M55A 示例路径永不匹配 bug、IntegrityError 误报为 topic 409（真因 `plan_versions.content_md NOT NULL`）、detail 读路径漏传 `plan_file_path` / `log_file_path` / `mode`。

## 实施 log

- Round 0：计划创建 + 评审两轮（r1 阻塞项「无门禁空洞」→ plan v2 配对门禁） → [log-r0.md](log-r0.md)
- Round 1 · M55A–F 实施、16 用例、wire 验证、三处新缺陷修复与 wire 事故处置（M55F 首个执行样例） → [log-r1.md](log-r1.md)

## 风险

- **422 handler 为全局行为变更**：`detail` 数组字节兼容（M54C 冻结测试无回归），新增字段为 additive；已通过 SDK/CLI 全链路验证
- **瘦身 stub 语义**：`plan_versions` v1 为自述 stub（指向 FS 文件），`plan_revise`（恒为全量内容）产生真实 v2+——契约在 `ExperimentCreate` 注释与测试双重固定
- **IntegrityError 分流按索引名匹配**（`uq_experiment_one_active_per_topic`），SQLite/PG 报文均含索引名；topic 冲突文案不变
- 范围外移交：`map experiment cancel` CLI 封装缺失（端点 + SDK 可用）；FS 话题（uuid5）不可被 `experiment.topic_id` 引用（r0 移交项维持）

## acceptance

1. **422 统一 handler + hint（缺字段 + 最小 payload）** ✅ `register_request_validation_handler` 落地；wire 实测缺 plan 字段返回 `error_code=request_validation_error` + `missing fields: plan`；experiments 端点附可复制 payload（r1 审查发现精确路径匹配 bug，已改后缀匹配并转绿）
2. **frontmatter 失败附完整模板片段** ✅ `_frontmatter_template_hint`：YAML fenced 块含 4 必填字段 + `dependencies: []` 注释；wire 实测 stderr 直接可复制
3. **dependencies 空列表语义** ✅ `[]` 通过校验（`test_dependencies_empty_list_passes`），缺 key 仍拦截，其余三字段非空维持（`test_other_fields_empty_list_still_blocks`）
4. **E8 server + CLI 配对门禁** ✅ server：slim 形态过门禁且 201（wire 实测）；CLI：合规瘦身计划通过 + 缺 frontmatter 瘦身计划 exit 2 本地拦截、零 HTTP 请求（`test_cli_slim_bad_plan_blocked_by_local_lint`）；`--force-lint-bypass` 语义不变；M54 Round 0 踩坑场景回归通过
5. **CLI hint 人类可读渲染，JSON 结构不变** ✅ Hint/Docs/Recover 分行、多行模板透传（`test_cli_yaml_error_renders_hint_block`）；JSON 错误信封字段集不变（`test_cli_json_error_envelope_shape_unchanged`）
6. **Skill 日志纪律条目** ✅ experiment-host SKILL.md 第 8 条；本轮 wire 事故按该条目完整记录（失败原文 + 处置 + 教训）
7. **pytest 覆盖 + 存量不回归** ✅ 16 新用例全绿；快速门控 455 passed / 2 skipped（基线 437）；实验域全量 109 passed；ruff 清零；1 个 HEAD 上既红的历史 fixture（frontmatter 迁移漏改）顺手修复
