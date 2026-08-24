---
title: "退役面物理收口：legacy 启动脚本 stub 化（两阶段第一阶段）+ CI 防回潮 + docs↔scripts 对照"
acceptance:
  - "A1 legacy 组 stub 化（v2 以矩阵+scripts/ 实况修正清单）：legacy 组 = `docs/LEGACY-ENTRY-MATRIX.md` scripts 节分类为 deprecated 的 4 个文件：`start-participant-bridge.sh` / `start-participant-bridge-claude.sh` / `start-reviewer-bridge.sh` / `start-reviewer-bridge-claude.sh`（`start-host-bridge*.sh` v0.10 已随 cli/host_worker* 整套删除，不在清单）。保留文件名，统一模板 stub：`echo \"退役: bridge 启动路径已停用(由 simple-waker 取代); 单 persona 等价替代: scripts/start-simple-waker.sh --persona <participant|reviewer>; 三 persona 入口: scripts/start-all-wakers.sh\" >&2; exit 1`——stderr 给出功能等价替代 + 非零退出。`start-all-wakers.sh` / `start-all-simple-wakers.sh` / `start-simple-waker.sh` 是矩阵主路径，**不在 stub 范围、保持不动**。实测：4 个脚本逐个执行 → exit 1 + stderr 指引"
  - "A2 两阶段边界：本实验只做第一阶段 stub（观察一个周期）；物理删除评估是后续裁决，**不在本实验验收内**——删除仅当脚本承载机密/明显误导且 stub 后仍可能被误启动时才考虑"
  - "A3 CI/lint 防回潮两条规则（v2 定稿登记基准）：(1) LEGACY-ENTRY-MATRIX 中已声明 deprecated 的入口仍为可执行且非 stub 内容 → fail；(2) 退役声明处（CLAUDE.md/Skill/wake.md）出现矩阵未登记的启动路径 → fail 提醒补登记。「已登记」的单一真相 = `docs/LEGACY-ENTRY-MATRIX.md`（scripts/pyproject/文档三面分类既有矩阵），CI 解析该矩阵取 deprecated 与主路径清单，**不在 CI 脚本内再造第二份清单**；矩阵更新走 A4 对照流程。实测：两类回归注入（stub 被改回可执行真脚本 / 声明处新增矩阵未登记路径）均能 fail"
  - "A4 docs↔scripts 对照（v2 收敛为矩阵更新，不产出平行对照表）：以 `docs/LEGACY-ENTRY-MATRIX.md` 为承载——A1 stub 化后核对矩阵 scripts 节与 `scripts/` 实况逐文件一致（分类、替代指引），矩阵缺失/过期的行在本实验内修正；`docs/MAP-SIMPLE-WAKER.md` 的启动路径声明与矩阵一致性同核。不另建新对照表文件，避免第二真相源"
  - "A5 物理删除人工确认纪律（participant round2 补充）：第二阶段删除走显式 diff 评审（host 之外至少一人看过删除清单）——流程纪律写进文档（如 SIMPLE-WAKER.md 或 CLAUDE.md 边界节），不进自动化"
  - "测试面：CI 规则的回归注入单测/脚本自测全绿；`ruff check` 通过"
evidence_keys:
  - "实测输出：4 个 legacy 脚本逐个执行 → exit 1 + stderr 指引全文（A1）"
  - "两次 CI/脚本运行记录：两类回归注入均 fail（A3，含「CI 读矩阵清单」的解析路径）"
  - "矩阵 diff：LEGACY-ENTRY-MATRIX.md 与 scripts/ 实况逐文件一致的核对照（A4，落在矩阵文件的修正 commit）"
  - "grep 核证：stub 模板统一（含功能等价替代命令指引）；删除人工确认纪律落位文档（A5）"
dependencies:
  - "话题 retired-surface-physical-cleanup（c1fa5b60-dfc0-5a5b-b0a5-472feddaeeef）close_note 口径：stub 化优先不默认物理删、统一模板、两阶段、CI 两条防回潮规则、docs 对照——participant 两轮表态无异议，附补充（删除阶段显式 diff 评审）已吸收为 A5"
  - "声明背景：CLAUDE.md（Waker 与职责边界）与 experiment-host Skill 多处声明「cli/host_worker bridge 与 legacy runtime-waker 启动路径已停用」，但 legacy 脚本仍在仓库——文档纪律对抗不了文件存在的事实；DB 写路径退役（v0.13 M58）做了 stub（_db_write_retired 引导性错误）为先例，脚本侧未跟进"
  - "participant 实战顾虑：waker 心跳里 map-agent / map-agent-2 两个 last_waker_poll_at=never 的角色配置仍在——误启动 bridge 会产生「看起来在跑、实际是僵尸」的通道，比不存在更糟；stub 把僵尸通道变明确死胡同"
  - "scripts/ 改动独立，与本批其他实验无代码冲突；不触碰 simple-waker 本体与三 persona 主路径（start-all-wakers.sh / start-all-simple-wakers.sh / start-simple-waker.sh 保持不动）"
---

# 退役面物理收口：legacy 启动脚本 stub 化 + CI 防回潮 + docs↔scripts 对照

## 背景

话题 `retired-surface-physical-cleanup`：CLAUDE.md / Skill 多处声明「host bridge 与 legacy runtime-waker 已停用」，但 `scripts/` 里 bridge 组脚本仍躺着——文档「勿依赖」对抗不了文件存在的事实，新 agent/人仍可能误启动，产生「看起来在跑、实际是僵尸」的通道（比根本不存在更糟）。

## 定稿决议（close_note + Round 2 双方表态）

| # | 决议 | 来源 |
|---|------|------|
| D1 | stub 化优先，不默认物理删除：保留文件名 + echo 指引 + exit 1——物理删使「路径不存在」运行时挂（隐藏依赖如 cron/习惯路径），stub 把僵尸通道变明确死胡同，失败可控且可读 | participant 核心立场采纳 |
| D2 | stub 模板统一 + 功能等价替代：文案点明等价替代命令（bridge 是单 persona 常驻语义 → `start-simple-waker.sh --persona <p>`；三 persona → `start-all-wakers.sh`），不留「启动了但功能不等价」的指引 | participant 边界 2 采纳，v2 按评审 08862d1c 修正替代命令语义 |
| D3 | 两阶段：先 stub 一批、运行一个周期确认无引用误启动，再评估是否删——不一次性删 | 双方一致 |
| D4 | CI/lint 兜底防回潮（本话题「真正改变惯性的部分」）：两条规则封住「stub 被改回」「新增未登记路径」两类回归 | participant 口径 3 采纳 |
| D5 | docs↔scripts 逐文件对照：「唯一启动路径」声明与实际可启动脚本逐一映射 | participant 口径 4 采纳 |
| D6 | 第二阶段物理删除走显式 diff 评审（host 之外至少一人看过清单），流程纪律不进自动化 | participant round2 补充采纳 |
| D7 | legacy 组清单以 `docs/LEGACY-ENTRY-MATRIX.md`（scripts 节 deprecated 分类）+ `scripts/` 实况为准 = 4 个 bridge 脚本；`start-all-wakers.sh` 是矩阵与 CLAUDE.md 中的主路径（三 persona simple-waker 默认入口），不在退役范围；`start-host-bridge*.sh` 已不存在，不枚举 | 评审 item 08862d1c（v1 计划评审）修订定稿 |
| D8 | 「已登记启动路径」的单一真相 = LEGACY-ENTRY-MATRIX.md：CI 规则(2) 解析矩阵取登记清单，不在 CI 脚本内硬编码第二份清单；A4 对照也收敛为矩阵自身的修正更新，不产出平行对照表 | 评审 item a3cdfe7f（v1 计划评审）修订定稿 |

## 实施顺序（建议，评审可调）

1. **I1 legacy 组 stub 化**（A1，按矩阵清单 4 个文件）：统一模板逐个替换，git 历史可追溯内容
2. **I2 CI/lint 规则**（A3，登记基准=矩阵解析）：两条规则 + 回归注入自测
3. **I3 矩阵对照更新**（A4）：LEGACY-ENTRY-MATRIX.md ↔ scripts/ 实况逐文件核对修正，SIMPLE-WAKER.md 一致性同核
4. **I4 删除纪律文档**（A5）：第二阶段人工确认流程写进文档
5. **I5 验证收尾**：4 个 legacy 脚本逐个执行验证 + CI 全绿 + ruff check

## 风险与边界

- stub 前核对隐藏引用（cron/systemd/文档内链）：发现仍被引用的脚本不盲 stub，先在实验日志登记再裁决（D3 观察周期的输入）
- 近名风险重述（v2 修正）：`start-all-wakers.sh`（主路径）与 `start-all-simple-wakers.sh`（主路径，被前者调用）近名——两者都保留不动；真正的退役对象是 4 个 `*-bridge*.sh`，stub 文案按 D2 给出等价替代
- CI 解析矩阵引入「矩阵格式即契约」：矩阵表格行格式变更会破坏 CI 解析——I2 实现时给矩阵格式加最小解析器容错（逐行正则而非全文件结构断言），并在矩阵文件头注释声明「此文件被 CI 消费，改格式先过 CI」
- 本实验不动 simple-waker 本体与三 persona 主路径；CLAUDE.md「start-all-wakers.sh 轮询 map work」表述与矩阵一致，无需核正（v1 表述有误，v2 已删）

## v2 修订说明（回应评审 913715b3）

- item 08862d1c：A1 清单修正为矩阵 deprecated 分类的 4 个 bridge 脚本（删不存在的 start-host-bridge*、剔出主路径 start-all-wakers.sh）；stub 模板替代命令改为功能等价语义（单 persona waker / 三 persona 入口分别指明）；D2/D7 定稿
- item a3cdfe7f：A3 规则(2) 登记基准定稿为 LEGACY-ENTRY-MATRIX.md 单一真相（CI 解析矩阵、不造第二清单）；A4 从「落盘新对照表」收敛为「矩阵自身修正更新」；D8 定稿；I2/I3 与 evidence_keys 同步
