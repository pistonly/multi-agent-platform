---
title: "experiment 域 CLI 卫生三合一：log 阶段放宽+友好报错 / --topic-id slug 路由+参数收敛 / pre-complete 收尾动线"
acceptance:
  - "A1 log 阶段放宽：`server/services/log_service.py` 白名单加入 draft/review/approved 三态后，draft 阶段 `experiment log --summary ... --file ...` 成功落库，且条目出现在实验 log 时间线并与 running 期日志连续排序（T1-P1；participant 验收 1）"
  - "A2 log CLI 友好报错：`--summary` 传了但内容文件（--file/--log-file-path）没传时，输出一行式错误（`Error: --summary requires --file or --log-file-path`，exit 2），无 pydantic ValidationError 堆栈（T1-P3；participant 验收 2）"
  - "A3 补偿流程退役（只删第 8 条后半句）：experiment-host Skill「日志纪律」条目删除**末尾「同轮日志被状态机拒绝时（如 review 阶段不能写 log），把日志文件落 FS（`map/experiments/<slug>/log-rN.md`）并在进入下一阶段后立即补记」这一后半句**；**前半「create / revise / submit 等任何一次失败后重试成功，都必须补一条 `experiment log` 记录失败原文（422/409 的 error_code 与 hint）与修复动作」保留**（v0.12 M55F E5 教训，与阶段白名单无关）。未核实到 execution-cookbook 存在「log-r0.md 落盘约定」章节（grep log-r0/log-rN/旁路/补记/落 FS 零命中），故不以任何文档的 cookbook 删除为验收内容（T1-P2 按 boundary 修正；participant 验收 3「文档同步项写进实验计划」）"
  - "A4 --topic-id 双路由：`experiment create --topic-id <slug>` 直接成功（无需先 `topic show` 抄 uuid）；FS 话题传 slug 解析为 uuid5（复用 `_resolve_topic_ref`），DB 话题 uuid 路径不回归（T2-P1；participant 验收 2）"
  - "A5 参数收敛 v1：fs 域命令接受 `--id` 具名别名且 `--topic` 双轨不 break；参数解析失败报 did-you-mean 形态（「你是不是想用 --id <slug>」）；抽 3 个高频命令（experiment create/status、topic comment、fs comment）在 help 与 error path 两侧核对具名一致性（T2-P2；participant 验收 1、3）"
  - "A6 收尾动线：pre-complete 成功输出末尾回显完整可粘贴的 complete 命令行（`experiment complete --id <uuid> --metadata <path> --log-file-path <path>`，参数按本次 pre-complete 入参填好）；complete 缺 metadata 时错误信息前置给出 accepted key 列表 + 示例 JSON 片段（不再只在失败后可见）；`--file` 与 `--log-file-path` 在 help 各配一行使用场景说明（T3-S1；participant 验收 1、2）"
  - "测试面：log_service 白名单放宽、CLI 前置校验、slug 双路由、别名双轨的新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "pytest_summary：log_service / experiment CLI 校验 / topic-id 路由 / fs 别名四组新增单测全绿"
  - "实测输出：draft 阶段 log 落库后的实验日志时间线（连续排序）；`--topic-id <slug>` 创建成功的命令输出；pre-complete 回显的 complete 命令行"
  - "grep 核证：experiment-host SKILL.md 已无 `log-rN.md` 字样残留，且「失败重试后…补一条 experiment log」前半句仍在（A3）；commands.md 示例语法与实测一致；host-checklist §3 顺序已修正为 create → close"
  - "help/error path 核对表：3 个高频命令的具名一致性逐条记录"
dependencies:
  - "主话题 experiment-log-phase-restriction（e9b4555d-6052-5ea2-bc80-0040d4fcc0d5）Round 2 定稿：log 放宽从 draft 起（host 提议、participant 明确「从 draft 起全放」）；M55 全信封改造不并入本实验（窄修复口径，双方无异议）"
  - "关联话题 cli-param-consistency（aab00372-ebc5-5c8c-9b2f-94c82988095a）Round 2 定稿：participant 收敛形态建议「--id 统一双路由、--topic 退役为 --id 兼容别名」以双轨过渡落地（过渡期内 --topic 不 break），存量改名留 major 版本窗口"
  - "关联话题 complete-metadata-duplication（bcd67e7a-df9d-517a-9288-030a4cff6174）Round 2 定稿：S1 先行；S2（pre-complete 签发 token）与 S3（deprecate pre-complete）不并入，participant 表态「方向 1 随实验跟进」→ 归 backlog"
  - "话题 fast-gate-allowlist-inversion audit_note 指派项：host-checklist §3 的 close→create 顺序描述与 409 门禁相反，文档核正并入本实验 P3（T2 文档核正范围内）"
  - "与实验 eb291c4b（fast-gate 反转，running）无代码依赖但有时序交互：本实验新增测试文件在 eb291c4b 反转完成前会被现白名单机制静默 deselect——若反转未先行完成，新测试需临时登记 _FAST_GATE_MODULES，反转落地后移除"
---

# experiment 域 CLI 卫生三合一：log 阶段放宽+友好报错 / --topic-id slug 路由+参数收敛 / pre-complete 收尾动线

## 背景

三个同批体验话题（2026-08-23 立项现场实测）收敛后落点高度重叠——`cli/commands/experiment.py` + `cli/main.py` + `server/services/log_service.py` 一带的 experiment 域卫生问题，合并为一次实验避免三次重叠改动：

1. **experiment-log-phase-restriction**（主话题）：draft/review/approved 阶段禁写实验日志，立项审计被迫走 `log-r0.md` FS 旁路 + 补记（Skill 日志纪律第 8 条后半句专为此存在，waker-heartbeat 实验再证实非边缘 case）；`experiment log --summary` 缺内容文件时抛 ~30 行 pydantic 原始堆栈
2. **cli-param-consistency**：`experiment create --topic-id` 在 typer 参数层声明为 `uuid.UUID`，slug 到不了路由逻辑直接类型错误（同一 CLI 内惯例断裂：topic 域 `_resolve_topic_ref` 早已双路由）；`--id`/`--topic`/位置参数三套风格并存，participant 实证「按文档惯例外推 → 自信写错」是 Agent 高频失败模式
3. **complete-metadata-duplication**：`metadata_has_completion_evidence` 同一谓词四处调用（CLI 两处 + server 两处），pre-complete 无持久化语义核验结果被丢弃；「complete 还要再传一遍 metadata」的要求只在失败后可见——每实验收尾 1-2 轮试探式重试

三个话题 Round 2 均 zero-objection 收敛，participant 修正点全部吸收（见 acceptance 注记）。

## 定稿决议（三话题 Round 2 双方表态合并）

| # | 决议 | 来源 |
|---|------|------|
| T1-P1 | log 白名单加 draft/review/approved（`server/services/log_service.py:33`，server 一处 + 单测）；**从 draft 起**全放——立项理由与评审期踩坑是审计链信息密度最高的段落，log 幂等追加无阶段排他性 | 双方一致（host 提议 draft 起，participant 明确支持） |
| T1-P2 | Skill 日志纪律第 8 条**后半句**（FS 旁路 log-rN.md + 补记）删除、前半（失败重试补 log 审计纪律）保留（净删文档半句） | 双方一致；participant 验收 3 点名「写进实验计划」；review revise 按 boundary 修正 |
| T1-P3 | log CLI 前置校验：`--summary` 无内容文件时一行式 Error（`cli/commands/experiment.py:569/576`，~5 行）；M55 全信封改造不并入 | 双方一致；participant「30 行堆栈进上下文只提取一行，token 成本与信噪比双差」 |
| T2-P1 | `--topic-id` 声明改 `str` + 复用 `_resolve_topic_ref`（`cli/commands/experiment.py:92`，~10 行）；FS 话题路由返回 uuid5——waker-heartbeat 实验（1b605e0b）已实证 FS 话题关联实验可行 | 双方一致 |
| T2-P2 | 参数收敛 v1：fs 域加 `--id` 具名别名（`--topic` 双轨不 break，退役留 major 窗口）；新命令一律具名禁止位置 id；did-you-mean 错误提示；**不做**存量立即改名 | host 保守边界 + participant「--topic 退役为别名」形态的双轨折中；participant 验收「过渡期不 break」即双轨 |
| T2-P3 | Skill 文档核正：commands.md 逐条核对示例语法；**host-checklist §3 顺序修正为 create → close**（fast-gate 话题 audit_note 指派项，409 门禁实测） | 双方一致 + fast-gate audit_note |
| T3-S1 | pre-complete 成功输出末尾**回显完整可粘贴 complete 命令行**（入参填好，对齐 M55 recovery_command 形态）；complete 缺 metadata 错误前置（accepted keys + 示例 JSON）；`--file`/`--log-file-path` help 各一行使用场景 | host S1 推荐 + participant 具体建议（回显命令行、help 场景说明） |
| T3-S2/S3 | 不并入：token 单传（S2）与 deprecate pre-complete（S3）归 backlog——S2 成本一个独立实验，S3 需 S2 裁决后再议 | host 光谱裁决；participant「方向 2 先落、方向 1 随实验跟进」 |

## 调研事实（Round 2 已核证）

| 事实 | 位置 |
|------|------|
| log 白名单三态：`running / pending result review / done`，draft/review/approved 全拒 422 | `server/services/log_service.py:33` |
| log CLI 直接构造 `ExperimentLogCreate`，缺内容文件的约束只活在 pydantic model 层；同函数 `--file`/`--log-file-path` 互斥已有友好前置校验（对照实现就在旁边） | `cli/commands/experiment.py:569/576` |
| `--topic-id` typer 声明 `uuid.UUID | None`，slug 在参数解析层即失败 | `cli/commands/experiment.py:92` |
| topic 域双路由既有实现：uuid → DB 优先（404 后本地反查 FS uuid5）；slug → FS 优先否则 DB slug | `cli/commands/topic.py:292`（`_resolve_topic_ref`） |
| completion evidence 谓词四处调用：CLI pre-complete / CLI complete / server ×2 | `cli/commands/experiment.py:317`、`cli/main.py:1310`、`server/services/phase_service.py:338/393` |
| `experiment complete --schema` 已能打印 metadata YAML 模板（半条改进已落地，剩余是可见性） | cli-ux PR1 |
| Skill 日志纪律第 8 条 = 前半（失败重试补 log）+ 后半（FS 旁路 log-rN.md + 补记）；**cookbook 无「log-r0.md 落盘约定」**（grep log-r0/log-rN/旁路/补记/落 FS 零命中）——删除面仅限 SKILL 第 8 条后半句 | `.cursor/skills/experiment-host/SKILL.md:48`；grep 复核实证 |

## 实施顺序（建议，评审可调）

1. **I1 log 放宽**（A1）：log_service 白名单 + server 单测——放最前是自举：后续 I2-I4 的执行日志可在 draft/review 阶段直接写平台，不再走 FS 旁路
2. **I2 CLI 报错与收尾动线**（A2+A6）：log 前置校验；pre-complete 回显命令行；complete 缺 metadata 错误前置 + help 场景说明
3. **I3 路由与别名**（A4+A5）：`--topic-id` 改 str + 双路由；fs 域 `--id` 别名 + did-you-mean
4. **I4 文档**（A3+T2-P3）：Skill 日志纪律第 8 条**后半句**删除（前半失败补 log 保留）；commands.md 核正；host-checklist §3 顺序修正
5. **I5 验证收尾**：help/error path 三命令核对表 + 新增单测全绿 + ruff check

## 风险与边界

- log_service 放宽是 server 侧改动：容器验证需 `docker compose build api`（build-time COPY，restart 不生效）
- 日志条目自带实验 phase 快照，review 期日志与 plan revision 不混淆——审计链只增不改，无破坏性；reviewer 若认为早期日志噪音，可在 result_review 裁决口径（本实验不预设）
- `--topic-id` 改 str 后 uuid 直传路径必须有回归测试（participant 实例 2 的标准动线不回归）
- 与 eb291c4b（fast-gate）并行执行的测试收集交互见 dependencies；文档改动限定 experiment 域章节，与 host-invoke 实验的编排章节不重叠
