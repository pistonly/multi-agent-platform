---
title: "M57 实验日志瘦身对称（v0.13）"
acceptance:
  - "ExperimentLogCreate 支持双形态：content_md（现有）与 file_path（新增）二选一（model_validator 对齐 PlanInput / ExperimentComplete 既有模式）；file_path 形态下 content_md 在 DB 中存 stub（\"See file: <path>\"），保持 experiment_logs.content_md NOT NULL 兼容"
  - "experiment_logs 表新增 file_path 列（Text, nullable）+ Alembic 迁移（幂等，存量行为不变）；ExperimentLogRead 透出 file_path 字段，detail 读路径可见（Read 为 ORMModel，file_path 列名与字段名一致则无 alias 工作，提交时以 detail 读路径测试当场验证）"
  - "CLI `map experiment log` 新增 `--log-file-path` 选项，与 `--file` 二选一（互斥校验，同传报错）；`--id` 复用 `_rid` 短 id 解析（既有行为不回退）"
  - "瘦身形态下的软校验处置有明确行为并测试锁定：evidence 校验基于 metadata（与 content_md 无关，瘦身形态行为与全量形态完全一致，测试锁定一致性）；内容相似度基于 content_md（瘦身形态跳过并在 validation 中标注 similarity_skipped: slim form，不误报不静默；summary 与 prior log summary 精确一致时输出提示而非警告）；瘦身形态下 --force-skip-similarity 为 no-op（测试断言不产生请求副作用或 server 忽略）"
  - "server 端 append_log 对 file_path 形态的行为有回归测试（stub 落库 / log_index 递增 / 读路径透出）；CLI 本地互斥与提示有测试；fast-gate 全量回归通过"
evidence_keys:
  - "pytest 新增 m57 瘦身测试全绿"
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：--log-file-path 瘦身形态追加日志成功且 detail 读路径可见 file_path；与 --file 同传报互斥错误"
dependencies:
  - "v0.13 提案评审通过（话题 v013-single-track-closure-review closed），M57 为 P0 首项"
  - "M55 E8 已确立瘦身形态先例：PlanInput.file_path 双形态 + CLI 本地 lint 补位（实验 84cccb2e done）；ExperimentComplete.log_file_path 与 TopicComment.file_path 提供追加型瘦身的列模式参照"
---

# M57 实验日志瘦身对称（v0.13）

> **plan v2**（2026-08-16）：按评审 r1 两项 unreasonable 修订——(1) 73a24940 M57D evidence 处置设计依据错误（evidence 校验基于 metadata 非 content_md，瘦身后不受影响，删除本地检查子项）；(2) 9277e044 相似度处置改为「跳过 + 标注 + summary 精确匹配提示」（summary 级相似度误报面更大）。非阻塞建议采纳：force_skip_similarity no-op 断言、ORM alias 当场验证、alembic 格式约束。

## 目标

按 docs/prd/v0.13.md §M57 补齐 F1：`experiment create --plan-file-path` 瘦身已先行（M55 E8），本实验给 `map experiment log` 补对称形态 `--log-file-path`。完成后实验计划与日志两个入口的输入形态完全对齐，长日志不再需要全量重传进 DB。

## 调研结论（2026-08-16 代码级核证，r1 评审复核确认）

| 事实 | 位置 | 对设计的影响 |
|------|------|--------------|
| `ExperimentLogCreate.content_md` 必填，无 file_path | `map_types/schemas/experiment.py:153` | 需加可选 `file_path` + 二选一 validator（照抄 PlanInput :18-22 模式） |
| `experiment_logs` 表无 file_path 列 | `server/domain/models.py:535-548` | 需加列 + Alembic 迁移；TopicComment.file_path（:357）提供同款先例 |
| `ExperimentComplete` 已有 `log_file_path` 双形态（存 Experiment 行） | 同文件 :282-294 | complete 是单值覆盖；log 是**追加型多行**，须用行级 file_path 列（不能用 Experiment 行） |
| append_log 两项软校验的数据源不同（r1 修正）：evidence 基于 `metadata` dict（`validate_log_evidence(plan_md, metadata)`，log_service.py:76），与 content_md 无关；相似度基于 `content_md`（`validate_log_similarity(db, content_md)`，与最近一条 prior log 比较，similarity_service.py:94-119） | `server/services/log_service.py:54-116` | **evidence 校验不受瘦身影响**（metadata 照常随请求发送，行为与全量形态一致）；**仅相似度需要瘦身处置**（见 M57D） |
| log 无 frontmatter 硬门禁（区别于 plan；marker 硬校验仅挂 create/revise） | project_service.py:290 / plan_service.py:121 | 瘦身形态无需复刻 M55D 的 server 门禁跳过逻辑，风险面更小 |
| CLI `experiment log --file` 必填；`--id` 已是 str + 短 id 解析（M54B） | `cli/commands/experiment.py:492-507` | 改为 `--file` / `--log-file-path` 二选一 |

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M57A schema 双形态 | `ExperimentLogCreate` 加 `file_path: str \| None` + `_require_content_or_path` validator（对齐 PlanInput 既有文案模式） | `sdk/python/map_types/schemas/experiment.py` |
| M57B DB 列 + 读路径 | `experiment_logs` 加 `file_path Text NULL`；Alembic 迁移（新迁移文件保持 ruff 格式干净，M59 将纳 lint）；`ExperimentLogRead` 加 `file_path: str \| None`（ORMModel，列名字段名一致免 alias，detail 测试当场验证）；append_log 在 file_path 形态下 content_md 存 stub | `server/domain/models.py` + `alembic/` + `map_types/schemas/experiment.py` + `server/services/log_service.py` |
| M57C CLI 选项 | `experiment log` 加 `--log-file-path`，与 `--file` 互斥（同传 exit 2 + 提示，对齐 create 的 --plan-file/--plan-file-path 互斥模式）；`--file` 语义不变；SDK `append_log` 透传新字段 | `cli/commands/experiment.py` + `sdk/python/map_client/`（SDK 方法签名） |
| M57D 软校验处置（r1 修订后） | **evidence：零改动**——校验基于 metadata，瘦身形态行为与全量形态完全一致（测试锁定一致性断言，不引入任何本地检查）。**相似度：跳过 + 显式标注**——file_path 形态跳过 content_md 相似度（stub vs stub / stub vs 全文比较均无意义且误报），validation 返回中标注 `similarity_skipped: slim form`；防滥用以审计代替误报警告：summary 与 prior log 的 summary **精确一致**（== 比较，非相似度）时输出提示（提示而非警告，不阻塞落库）；**force_skip_similarity：no-op**——瘦身形态下无相似度警告可跳，CLI 不发送该字段 / server 忽略并返回提示，测试断言无请求副作用 | `cli/commands/experiment.py` + `server/services/log_service.py` |
| M57E 测试 | server：file_path 形态 append（stub 落库 / log_index 递增 / Read 透出 / 相似度跳过标注 / summary 精确一致提示）+ evidence 行为一致性（同 metadata 下瘦身与全量 validation 相同）+ 双形态二选一 422 + force_skip_similarity no-op + 存量 content_md 形态回归；CLI：互斥报错、`--file` 回归 | `tests/test_experiments.py`（或新文件归属实验域）+ `tests/test_cli_experiments.py` |

## 实现顺序

1. M57A + M57B（schema 与 DB 同批，Alembic 迁移先行）
2. M57D server 侧（append_log 相似度分支与 summary 精确匹配提示，随 B 提交）
3. M57C CLI + SDK 透传
4. M57E 测试补齐 + fast-gate 全量回归
5. CLI 实测（evidence 第 3 条）+ wire 验证（8001 已于实验创建时重启至最新代码）

## 风险与对策

- **相似度检查被瘦身绕过**（agent 用 file_path 规避重复日志警告）：以审计代替误报警告——summary 与 prior log summary 精确一致时输出提示（非警告）；提示内容含两轮 summary 全文供 agent 自行判断，不阻塞落库；r1 评审定案（9277e044），不再采用 summary 级相似度（同主题多轮 summary 天然高度相似，70% 阈值下必然常触发，警告贬值成噪音）
- **evidence 校验行为漂移认知风险**（后续维护者误以为瘦身后 evidence 失效）：M57E 的 evidence 一致性测试显式锁定；调研表与验收的表述以 metadata 为数据源留痕
- **Alembic 迁移与运行中 server 的兼容**：新列 nullable，旧代码写路径不受影响；迁移脚本 up/down 幂等；新迁移文件保持 ruff 格式（M59 将纳 lint 范围）
- **CLI 互斥设计**：不做「都不传则报错」之外的隐式默认（`--file` 现有用户零感知；两者同传 exit 2 提示二选一），避免 M56 参数类型放宽式的 breaking

## 开放问题（r1 后状态）

1. ~~相似度折中方案选哪个~~ **已定案（r1 评审 9277e044）**：跳过 + 显式标注 + summary 精确匹配提示；不采用 summary 级相似度。
2. ~~是否需要 excerpt~~ **已定案（r1 评审同意）**：不加，summary 字段已承担列表摘要职能，避免冗余列。
