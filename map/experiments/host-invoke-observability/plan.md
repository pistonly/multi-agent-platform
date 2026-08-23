---
title: "host invoke 编排可观测性 v1：--timeout 取消通知 / --follow 流式 / 会话状态显式化 / Skill 并行编排示例"
acceptance:
  - "A1 --timeout 生效：到点 CLI 输出友好报错（含已等待时长与被调方 session 状态，无堆栈），**且被调 persona 收到一条取消 notification（wakeable 通道，可验证）**——超时语义是「通知对方取消」而非静默杀进程（participant 修正 3 / 验收 2）"
  - "A2 --follow 流式：被调方阶段性输出实时打到 stderr（orchestrator SDK stream 事件透传回调），stdout 仍只在结束时含最终结果——stdout=数据 / stderr=人类可读约定不破（host L1）"
  - "A3 会话状态显式化：invoke 启动时输出一行目标 session 状态（waiting-for-session / running 之二），消除「在等已有会话」vs「没人接」歧义（host L1 状态行 + participant 修正 4 术语）"
  - "A4 prompt 上下文约定：文档约定 invoke prompt 首段固定放对象引用（topic slug / experiment id / 期望 skill 名），被调方可跳过自主发现目标环节（participant 修正 1，零状态机成本）"
  - "A5 Skill 并行编排示例：experiment-host execution-cookbook 补「双 invoke 后台并发 + 汇合点」模式，汇合点查询走平台对象（`map work` / `topic show` / `experiment status`——进度回流的真实信道是平台对象而非 stdout 轮询，participant 修正 2）；示例可直接复制执行（participant 验收 3）"
  - "测试面：orchestrator/CLI 层新增单测（timeout 路径、启动状态行、stderr/stdout 分离）全绿；`ruff check` 通过"
evidence_keys:
  - "实测输出：--follow 会话中途 stderr 可见阶段输出的截录；--timeout 触发的报错全文 + 被调方 notification 记录"
  - "pytest_summary：orchestrator timeout / 状态行 / 流式分离单测全绿"
  - "grep 核证：execution-cookbook 并行编排示例存在、语法可直接复制执行；prompt 首段对象引用约定入文档"
dependencies:
  - "话题 host-invoke-async（84fcff48-db76-56a1-b85b-645b556184a6）Round 2 定稿：L1（纯 CLI 层三件套）+ L2（Skill 并行示例）立项；L3 平台任务模型暂不立项、写 backlog——participant 表态「支持 --async + invoke-status 方向」与其建议验收 1（invoke-status 四态）经 host 裁决归 L3 backlog，待 L1 把「卡死无诊断」解决后按编排频率重估"
  - "participant 被调方视角四点修正全部吸收：prompt 结构化上下文（A4）、进度回流走平台对象（A5 汇合点设计）、--timeout 取消通知语义（A1）、会话状态显式化（A3）；其中修正 2 的完整形态（invoke-status 聚合平台对象事件流）属 L3，本实验只落汇合点查询部分"
  - "与实验 eb291c4b（fast-gate 反转）和 cli-hygiene-batch 无代码依赖：本实验只动 cli/orchestrator.py 及 experiment-host Skill 编排章节（cli-hygiene-batch 动 experiment 域命令与日志纪律章节，不重叠）"
---

# host invoke 编排可观测性 v1：--timeout 取消通知 / --follow 流式 / 会话状态显式化 / Skill 并行编排示例

## 背景

话题 `host-invoke-async`（2026-08-23 两次实验编排实测）：host 编排模式（`map --persona host host invoke --persona <p> --prompt ...`）同步阻塞单次 3-8 分钟，输出只在进程退出时可见、无超时、并行编排靠民间偏方（run_in_background 自管）——编排器职责推给调用方。

Round 2 host 核证阻塞点在**进程级包装**：`cli/orchestrator.py:238` 的 `run_invoke` 用 `asyncio.run(_run())` 同步化，orchestrator 内部本就在消费 SDK stream 事件——**不是 SDK 不产流，是 CLI 层把流吞了**。修复大部分在 CLI 层，无平台改动。

participant（最常被 invoke 的一方）Round 2 表态支持方向，并从被调方视角贡献四点修正，全部吸收进验收（见 acceptance 注记）。

## 定稿决议（Round 2 双方表态合并）

| # | 决议 | 来源 |
|---|------|------|
| D1 --timeout | `asyncio.wait_for` 包裹 + 超时友好报错（含已等待时长与对方 session 状态）；**语义按 participant 修正：到点同时向被调方发取消 notification（wakeable 通道，机制已存在）**，被调方至少能决定是否中断——最坏体验是静默放弃，被调方白跑并写完产出 | host L1 + participant 修正 3 |
| D2 --follow | orchestrator 消费 SDK stream 事件处加透传回调，阶段性输出实时打 **stderr**；stdout 保持结果纯净 | host L1（对齐平台 stdout/stderr 约定） |
| D3 启动状态行 | invoke 启动输出「目标 session 状态：waiting-for-session / running」；`--new-session` 与默认等复用的二值语义显式化 | host L1 + participant 修正 4 |
| D4 prompt 上下文约定 | 约定 prompt 首段固定放对象引用（topic slug / experiment id / skill 名），被调方跳过「whoami → 读 Skill → map work → 自主发现目标」的冷启动大头 | participant 修正 1（其备选 `--context` 参数同等效果，v1 取文档约定更轻；实现侧若顺手可加透传拼接，非验收项） |
| D5 L2 并行编排示例 | execution-cookbook 补「双 invoke 后台并发 + 汇合点」；汇合点查询走平台对象（participant 修正 2：被调方中间产出本来就写平台——topic comment / experiment log / notification——查询既有对象优于转发原始 stdout） | host L2 + participant 修正 2、验收 3 |
| D6 L3 暂不立项 | `--async` + task id + `invoke-status` 四态 + 平台对象事件聚合：需要 server 侧任务持久化、断连恢复、状态机——成本一个完整实验；L1 解决「卡死无诊断」后其剩余收益（异步发起）对当前编排频率不构成痛点。写 backlog 话题承接 | host 裁决；participant 建议验收 1 归此 |

## 调研事实（Round 2 已核证）

| 事实 | 位置 |
|------|------|
| 阻塞点：`run_invoke` 用 `asyncio.run(_run())` 同步化，CLI 进程挂起等 `HostOrchestrator.invoke` 的 await；stdout 集中打印，流被 CLI 层吞掉 | `cli/orchestrator.py:238` |
| 会话冲突二值语义：`--new-session`（强制新会话）vs 默认（等进行中会话），等待时长与对方进度均不可见 | 话题发起帖第 4 点坐实 |
| notification wakeable 通道已存在（取消通知无需新管道） | participant 修正 3「机制已存在」 |
| 触发频率：两次实验的 participant/reviewer 唤醒全走 invoke，每次 3-8 分钟 × 4-6 次 | 话题发起帖备注 |

## 实施顺序（建议，评审可调）

1. **I1 状态行 + timeout**（A3+A1）：D3 状态行（读 session 状态处就在 orchestrator 现有逻辑）；`asyncio.wait_for` 包裹 + 取消 notification 发送
2. **I2 --follow**（A2）：stream 事件透传回调 → stderr；stdout 纯净性单测
3. **I3 Skill 文档**（A4+A5）：prompt 首段对象引用约定 + 双 invoke 并发/汇合点示例（汇合点查平台对象）
4. **I4 验证收尾**：实测三件套（follow 中途可见 / timeout 报错+notification / 状态行）+ 单测全绿

## 风险与边界

- 取消 notification 的发送通道复用既有 notification 创建路径，无 server schema 改动；若实测发现 CLI 无直连创建入口，允许最小 server 端点补充（超出即回 plan revise）
- `--follow` 打 stderr 的内容含被调方工具调用噪声——定位是「诊断卡在哪一步」而非结构化进度；结构化进度（平台对象聚合）是 L3 范畴，D6 已划界
- timeout 触发后被调方会话仍在跑：notification 是「通知取消」不是「强制终止」——强制终止涉及 runtime 生命周期管理，明确不在 v1
