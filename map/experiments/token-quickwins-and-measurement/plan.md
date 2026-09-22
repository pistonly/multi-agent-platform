---
title: "token 开销阶段一：CLI 输出安全速赢（work/topic list/experiment 内联回吐/traceback/whoami 冗余）+ 轻量 CLI 记账测量面（v3：吸收 reviewer v2 评审 3 条 unreasonable）"
acceptance:
  - "A1 `map work` 默认视图精简，**固定状态夹具快照测试为主判据**：夹具=空 todos 分区+空通知+固定 agent 块+固定 persona 心跳数，断言渲染输出 ≤800B；before/after 实测对比（2078B→）降级为 evidence 补充证据；`--verbose` 恢复完整诊断视图；**`--json` 输出与改前逐字段一致（契约测试固定）**"
  - "A2 whoami 冗余消除**三处真相源同步**：wake.md「唤醒后四步」第 1 步改为直接 `map work`（agent 块内置身份确认，身份存疑才单独 whoami），**同时修改 `cli/agent_client.py` waker 集成系统提示词**（现 419-429 行仍指令 Agent 跑独立 whoami 且读 `todos` 而非 wake.md），**同时修改 `AGENTS.md`「Waker 与职责边界」表的唤醒流程行**（`whoami → map work → 写回 MAP` 改为 `map work（agent 块即身份）→ 写回 MAP`；AGENTS.md 不在 RUNTIME_CONTRACT_FILES 清单，改后需核对 CLAUDE.md 符号链接仍解析）——三处一致化，测试断言 agent_client 提示词文本含 work 优先指令、不含独立 whoami 指令；persona SKILL.md 硬性规则「先 persona whoami 确认身份」（experiment-reviewer:32 / topic-participant:38 / experiment-host 等）**列为联动评估项**：措辞统一为「whoami 或 work 的 agent 块任一确认身份」，实施时核对 test_red_line_clause.py 与 RUNTIME_CONTRACT 联动后落定；**kind-dispatch 生成标记块逐字节不变**（`map work --kinds --kinds-format md` 输出 vs 块内 diff 为空，沿用 d559f431 A5 口径）"
  - "A3 `map topic list` 默认只列 open 话题，`--status all` 恢复全量；**`--json` 与改前逐字段一致（契约测试）**；组合语义定死：`--status all` 只影响人类可读默认视图，`--json` 始终全量（机器消费不受默认视图影响）——写进 file-reference/commands 分发面说明"
  - "A4 `experiment show`/`status`/`complete` 默认输出不再内联 plan content_md 全文（只给 plan_version_count + plan_file_path 指针 + summary），`--full` 恢复全文，log 回显同理裁剪；**`--json` 与改前逐字段一致（契约测试）**；`--full`/`--json` 组合语义同 A3 定死；**分发面同步**：experiment-reviewer/SKILL.md 瘦身模式说明补 `--full` 逃生口（不改语义只加指引）；**运行时契约义务**（该文件 ∈ RUNTIME_CONTRACT_FILES，哈希随正文变化而 SimpleWaker 只在 `__init__` 计算缓存）：(a) 实施时按 docs/MAP-SIMPLE-WAKER.md 语义评估是否同步升 `RUNTIME_CONTRACT_VERSION`（评估结论写入实验 log，升或不升都要给理由）；(b) 验收含「**改后重启本机 waker（daemon restart）并观察一个 cycle 无 drift 误判**」——防仍在跑的 waker 持旧哈希把新内容判成 drift 按旧内容静默回写"
  - "A5 run-map.sh 落点双层定义：本机实例 `.map/run-map.sh` 补清 PYTHONHOME/PYTHONPATH（与 proxy 清理并列）；**分发面新增 `docs/map-templates/run-map.sh.example` 模板**（含补清后的完整脚本），AGENTS.md/QUICKSTART 等引用 run-map.sh 处指向模板；构造 PYTHONHOME 污染环境冒烟：wrapper 下 `map persona whoami` 正常返回"
  - "A6 网络不可达（Connection refused）时 CLI 人类可读输出 ≤3 行友好错误（含 server 启动命令提示），无 Rich traceback；`--debug` 时保留 traceback；**`--json` 模式错误形态定义**：单行机器可读结构（固定字段 `error`/`message`/`hint`）+ 固定非零 exit code，附契约测试——脚本消费方可判定失败"
  - "A7 CLI 记账测量面（字段对齐 Round Summary 阶段二）：每次 `map` 调用在命令出口落一行 JSONL 到 `.map/usage/cli-calls.jsonl`：`{ts, persona, kind, cmd, output_bytes, exit_code}`（kind=唤醒上下文传入的 work item kinds，非唤醒调用为 null；落点不用 perf-baselines 的理由：perf-baselines 在 .map/ allow-list 内被 git 追踪、承载基线对比快照，usage 是持续追加运行数据，放整目录 gitignore 的 .map/usage/ 免污染 git）；聚合查询提供**单一机器可判路径**：`map usage summary --since <ts>` 子命令输出会话窗内 `{persona, work_calls, total_output_bytes}`——「或脚本说明」删除"
  - "A8 测试面：各 I 新增单测全绿、现有测试 0 回归（尤其 **A1-A4 四处 --json 契约测试**与 work kinds 一致性测试）、`ruff check` 0"
evidence_keys:
  - "before/after 输出字节数对比（补充证据，非主判据）：work（2078B→）、topic list（1488B→仅 open）、experiment show（含 6KB plan 全文→指针）三组实测"
  - "JSONL 记账样本行 + `map usage summary --since` 一次唤醒会话窗口聚合输出"
  - "PYTHONHOME 污染环境冒烟输出（A5）与网络错误冒烟输出（A6）"
  - "agent_client.py waker 提示词修改前后 diff（A2 双真相源消除）"
  - "pytest 全量绿 + ruff 0 的运行记录"
dependencies:
  - "话题 skill-token-optimization（4ac47678）：Round 1 双方表态 + Round Summary 三阶段决议——本实验=阶段一+阶段二；#1/#2/#3/#4 结构性改动明确不在范围（待测量面数据回填后另立实验）"
  - "硬约束（participant round1 立场 + reviewer v2 补强）：`--json` 机器契约不动，且 A1-A4 四处都要契约测试覆盖，不只 A1；--full/--status all 组合语义写进分发面"
  - "基线数据（host round1 实测 + participant #11）：work 空闲态 2078B；topic list 1488B（closed 占 85%）；experiment 域 plan 内联回吐 ~6KB/次"
  - "先例：输出格式契约测试（v0.12 M54A --format）、work kinds 生成块 CI 校验（d559f431 A3）——本实验复用其测试模式"
  - "reviewer v2 修订依据：评审 65d85aca 的 5 条 unreasonable 逐条吸收（A2 双真相源 / --json 覆盖 / A5 落点 / A7 字段与可判定性 / A1 fixture）"
  - "reviewer v3 修订依据：评审 1b781052 的 3 条 unreasonable 逐条吸收——A2 补第三真相源 AGENTS.md:21（CLAUDE.md 符号链接，随唤醒会话无条件注入，不改则 #7 的 280B×2 省不下来且与新版 wake.md 矛盾）；A4 补 RUNTIME_CONTRACT_FILES 义务（哈希变化 + waker `__init__` 缓存 → 评估升版本 + 改后重启观察 cycle）；A6 补 --json 错误形态（单行 error/message/hint + 固定 exit code）"
---

# token 开销阶段一：CLI 输出安全速赢 + 轻量记账测量面（v3）

## 背景

话题 `skill-token-optimization` 审计结论：MAP 唤醒链路 token 浪费集中在 Skill 静态必读量（结构性，阶段三处理）与 CLI 输出不面向 Agent（本实验）。CLI 层是零正确性风险速赢。本版为 reviewer v2 评审 3 条 unreasonable 修订：A2 补第三真相源（AGENTS.md 唤醒流程行）、A4 补 RUNTIME_CONTRACT_FILES 契约义务（版本评估 + waker 重启观察）、A6 补 --json 错误形态定义。

## 实施项

| I | 改动 | 层面 |
|---|------|------|
| I1 | `map work` 默认视图裁剪 + `--verbose`；夹具快照测试 | cli 渲染层 + tests |
| I2 | whoami 冗余消除**三处一致化**：wake.md 四步 + `cli/agent_client.py` waker 系统提示词 + `AGENTS.md` 唤醒流程行；persona SKILL「先 whoami」措辞为联动评估项 | Skill 手写正文 + cli + AGENTS.md |
| I3 | `topic list` 默认 open，`--status all` 全量；组合语义进分发面 | cli/commands/topic |
| I4 | experiment show/status/complete 去内联回吐，`--full` 逃生口；reviewer SKILL.md 同步（触发 RUNTIME_CONTRACT 义务：评估升版本 + 改后重启 waker 观察 cycle） | cli/commands/experiment + 分发面 |
| I5 | run-map.sh 本机补清 + **docs/map-templates/run-map.sh.example 分发模板** + 引用文档指向 | .map/ + docs/map-templates |
| I6 | CLI 网络错误一行化 + `--debug` traceback + **`--json` 错误形态（error/message/hint + 固定 exit code）** | cli 全局异常处理 |
| I7 | CLI 出口记账 JSONL + `map usage summary --since` 聚合命令 | cli 出口装饰层 + 新子命令 |

## 实施顺序

I6 → I1 → I3 → I4（**完成后重启本机 waker 并观察一个 cycle**）→ I7（记账尽早落位）→ I5 → I2。每个 I 独立窄 commit（`map exp 4e4206de: I<n> …`）。

## 验证

- 单测：各 I 对应 tests/test_*.py；A1-A4 四处 --json 契约用固定快照测试；A1 状态夹具断言 ≤800B
- 冒烟：A5/A6 环境构造实测；before/after 字节对比落实验 log（evidence）
- 回归：pytest 全量 + ruff；wake.md 生成块 diff 校验沿用现有 CI 口径

## 非目标

- #1 kind→Skill 路由重构、#2 增量读、#3 description 瘦身、#4 历史包袱收编（阶段三，另立实验）
- `--json` 契约任何字段变更（`--full`/`--status all` 只影响人类可读视图）
- Agent Read 文件行为的观测（runtime 侧，后续阶段）
