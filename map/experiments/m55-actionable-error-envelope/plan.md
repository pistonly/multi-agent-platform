---
title: "M55 错误信封可执行化（v0.12）"
acceptance:
  - "FastAPI RequestValidationError（422）走统一 handler：响应附 hint 字段（缺失字段 + 最小 payload 示例），字段名与 StateTransitionError 既有结构（detail/error_code/hint/retryable）一致；近邻匹配（'title/accpetance 疑似拼写'类提示）作为降级增强，优先级让位于 hint 主战场"
  - "plan frontmatter 校验失败的 hint 附完整模板片段（YAML fenced 块，含 4 必填字段示例），agent 可直接复制修复"
  - "dependencies 支持空列表语义：dependencies: [] 通过校验（无依赖即显式空），不再被迫写 - none 哨兵值；其余三字段维持非空要求"
  - "E8 修复：--plan-file-path 瘦身模式过 frontmatter 门禁（file_path 形态 content_md 为空不再误报 STATE_MACHINE_PLAN_MARKER_MISSING），回归 M54 Round 0 踩坑场景"
  - "CLI 侧错误信封渲染 hint 为人类可读块（yaml 默认输出含 hint/docs_url/recovery_command 分行；--format json 输出结构不变，进 M54C 冻结字面量护栏）"
  - "experiment-host Skill 增加日志纪律条目：创建/提交失败重试必须记 experiment log（含 422 原文与修复动作），不依赖会话记忆"
  - "新增 pytest 覆盖（422 hint 结构 / 模板片段 / dependencies 空列表 / E8 瘦身路径 / CLI hint 渲染），现有 439 测试不回归"
evidence_keys:
  - "pytest 新增 m55 测试全绿"
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：故意触发 422（缺字段 frontmatter）输出含 hint 与模板片段；瘦身模式 create 不再误报"
dependencies:
  - "v0.12 提案 reviewer 两轮核对确认（话题 v012-ergonomics-review round2 收敛 ready）"
  - "M54 机器可读输出已交付（实验 f4ef8cb2 done）：CLIErrorEnvelope 与 cli-json-output.md 契约是本实验错误信封的渲染基座"
---

# M55 错误信封可执行化

## 目标

按 docs/prd/v0.12.md §M55 让错误响应成为 agent 可直接执行的修复指令：字段级 422 附最小 payload 示例、frontmatter 失败附完整模板片段、依赖语义合理化、瘦身模式过门禁（E8）、CLI 把结构化 hint 渲染为人类可读块。收敛 v0.12 评审确认的 E6（dependencies 哨兵值）与 E8（瘦身误伤）两个缺口。

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M55A 422 hint 结构 | FastAPI `RequestValidationError` 注册全局 handler：保留默认 detail 数组，附 `hint`（缺失字段名 + 端点最小 payload 示例）；近邻匹配（编辑距离 ≤2 的字段名提示）为降级增强，不阻塞主战场 | `server/main.py`（新增 handler，与 `register_domain_exception_handlers` 并列） |
| M55B frontmatter 模板片段 | `assert_plan_frontmatter_ok` 失败 hint 从字段清单升级为完整模板片段（fenced YAML 块 + 4 字段示例 + 当前缺失标注） | `server/services/plan_marker_service.py` |
| M55C dependencies 空列表 | `_collect_warnings` 对 `dependencies` 允许空列表（显式无依赖）；title/acceptance/evidence_keys 维持非空 | `server/services/plan_marker_service.py` |
| M55D E8 瘦身门禁 | `create_experiment` 的 frontmatter 断言区分输入形态：`plan_file_path` 瘦身形态跳过 content_md 校验（server 无本地文件），全量形态维持校验 | `server/services/project_service.py:285` |
| M55E CLI hint 渲染 | 错误信封 yaml 渲染含 hint/docs_url/recovery_command 分行块；json 输出结构不变（M54C 契约），渲染进冻结字面量护栏 | `cli/main.py` 错误输出路径 |
| M55F Skill 日志纪律 | experiment-host SKILL.md 增加「创建失败重试须记 log」条目 | `.cursor/skills/experiment-host/SKILL.md` |

## 实现顺序

1. M55B/C/D（纯 server 语义层，单一 service 文件族，先行打通门禁语义）
2. M55A（422 全局 handler，依赖 B 的模板工具函数复用）
3. M55E（CLI 渲染，依赖 A-D 的服务端 hint 结构定型）
4. M55F（Skill 文档，随 M55E 一并提交）
5. 全量回归 + 端到端实测（evidence 第 3 条）

## 风险与对策

- 422 handler 改动影响所有端点的校验失败响应形状：SDK/CLI 已有消费方按 `detail` 数组解析——handler 保留原 detail 结构仅追加字段（加法不改写），FastAPI TestClient 全量回归验证
- dependencies 空列表放开可能与下游解析（plan lint / web 渲染）耦合：搜索全部 `REQUIRED_PLAN_FIELDS` 消费方逐一核对，空列表在渲染层等价于 absent
- E8 跳过校验可能放行无 frontmatter 的瘦身计划：瘦身形态由 CLI 侧本地 lint 预检兜底（`--force-lint-bypass` 语义不变），server 侧在 plan_revise（有完整 content）时仍强制校验
- CLI 渲染块与 M54C 冻结测试冲突：只加不改（json 分支 byte-for-byte 不动），新渲染进独立测试

## 验收演示脚本

```bash
# 缺字段 frontmatter → 422 hint 含模板片段
map --persona host experiment create --title "t" --plan-file bad.md
# 瘦身模式不再误报（E8 回归）
map --persona host experiment create --title "t" --plan-file-path ./good.md
```
