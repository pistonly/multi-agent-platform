---
title: "M59 性能基线与工程卫生（v0.13）"
acceptance:
  - "F4：`scan_plane` perf 基线入库——`.map/perf-baselines/fs-scan-plane-baseline.json` 存在且含话题/实验计数与 p50/p95/p99 单次 scan 耗时；测量以可重复测试承载（`pytest tests/test_fs_scan_plane_perf_baseline.py -m slow`，同 phase1 基线机制：多次迭代取分位数、测试自身写产物文件）；`sdk/python/map_fs/parser.py` `scan_plane` 函数 docstring 含触发条件注释（话题 >500 或单次 scan p95 >100ms 再引入 mtime 增量，引入前先复测基线对比）"
  - "F5：lint 盲区收口——`ruff check server cli sdk scripts tests alembic test_project` 零错误（当前 alembic 172 + test_project 1 = 173，172 可 --fix，1 处 SIM105 手工改 contextlib.suppress）；`.github/workflows/ci.yml` 与 `nightly.yml` 的 ruff check 行纳入 `alembic test_project` 两目录；回归锁定测试入库（断言 CI workflow 的 ruff 行覆盖两目录，防止再滑出）且通过；alembic 改动后 `alembic upgrade head` 迁移可执行（import 层零回归）"
  - "F6 设计结论已决并回写 PRD v0.13（证据清单行 + 风险表行带已决标注）：不做——依据 (a) M58 后话题域主标识为 slug（fs 命令族全走 --topic <slug>，人类可读且项目内唯一，uuid5 前缀相较 slug 无增量价值）；(b) DB 存量 4 话题全 closed 零写路径，短 id 服务对象只剩只读遗留查询；(c) 实施成本中等（matcher 需 DB prefix query + FS plane 内存扫描双源，server id_prefix + SDK 透传 + CLI 分支三处），不满足 PRD「低成本且评审通过」实施门槛；(d) 重开条件：FS 话题规模增长导致 slug 输入成本成真实痛点"
  - "F7 设计结论已决并回写 PRD v0.13（同上两处带已决标注）：维持 no-op + 提示——依据 (a) FS 待办是 `fs_topic_progress_for_agent` 即时投影（derive_work 文件存在性推导，无 Notification 行），不存在可持久 dismiss/read 的对象；(b) 清理语义内建于 FS 本身（写 round<N>-<persona>.md 即清 pending_topic_reply、host 推轮即清 round_ack），建 id 映射 + DB 已读存储等于引入第二套状态，违背 v0.11「FS 是事实源」；(c) dismiss/read/mark-seen 继续服务实验域与存量话题通知（DB 投影），功能本身不退役"
  - "全量 fast-gate 回归通过（无新增失败）；PRD v0.13 M59 章节补执行状态标注"
evidence_keys:
  - "基线产物文件存在于 .map/perf-baselines/ 且测量测试可重复执行"
  - "ruff check 全仓（含 alembic test_project）零输出错误；CI workflow 两处 ruff 行 diff 可见两目录"
  - "alembic upgrade head 在 dev 库可执行（import 零回归）"
  - "PRD v0.13 F6/F7 已决标注 diff"
  - "pytest fast-gate 全量通过"
dependencies:
  - "v0.13 提案评审通过（话题 v013-single-track-closure-review closed）；M57/M58 已完成"
  - "phase1/phase2 基线机制先例（tests/test_waker_phase1_acceptance.py:420-443：测试写 JSON 产物到 .map/perf-baselines/）"
---

# M59 性能基线与工程卫生（v0.13）

## 目标

按 docs/prd/v0.13.md §M59 落地 F4/F5，F6/F7 出设计结论：`scan_plane` 性能基线入库（观测先于优化，YAGNI）；alembic / test_project lint 盲区收口并纳入 gate；两项悬置设计决策（topic 短 id 第三分支、fs 通知 id 映射）以「结论回写 PRD」形态结项，不强制实施。

## 调研结论（2026-08-18 代码级核证）

| 事实 | 位置 | 对设计的影响 |
|------|------|--------------|
| `scan_plane` 每请求全量遍历 `map/`（topics + experiments 目录逐个 parse frontmatter），无缓存 | `sdk/python/map_fs/parser.py:365-381` | F4 只记基线与触发条件，不预做 mtime 增量（PRD 设计原则 3） |
| 基线机制先例：phase1 acceptance 测试跑 N 迭代取 p50/p95/p99，写 JSON 产物到 `.map/perf-baselines/`（含 captured_at 与 note） | `tests/test_waker_phase1_acceptance.py:420-443`、`.map/perf-baselines/phase1-p95-baseline.json` | F4 复用同机制：新测试 + `fs-scan-plane-baseline.json` |
| gate 位置：`.github/workflows/ci.yml:41` 与 `nightly.yml:28` 的 `ruff check server cli sdk scripts tests`——alembic / test_project 从未在列（ruff exclude 只有 reference/tmp，属「从未覆盖」非显式豁免） | `.github/workflows/` | F5 = --fix + 1 手工 + gate 行补目录 + 回归锁定 |
| lint 现状实测：alembic 172（UP007 ×102 / UP035 ×34 / I001 ×35 / SIM105 ×1）+ test_project 1（I001）= 173；172 可 `--fix` | ruff --statistics（2026-08-18） | 与 PRD 记录一致；SIM105 是唯一手工项（try/except/pass → contextlib.suppress） |
| 短 id 先例：experiments 走 `cli/shortid.py resolve_ref`（通用 ref+matcher helper，明确「serve future topic/agent without experiment-specific imports」）+ server `id_prefix` Query（`server/api/experiments.py:118` → `svc.list_experiments(id_prefix)` DB 层 LIKE 前缀查询） | `cli/shortid.py`、`server/api/experiments.py`、`server/services/project_service.py:424-439` | F6 若做需三处改动；但话题侧 matcher 必须双源（DB 行 + FS uuid5 内存扫描），复杂度高于 experiments 先例 |
| M58 后话题域主标识 = slug：fs 命令族（comment/advance-round/close/show/topic-create）全部 `--topic <slug>`；FS uuid5 由 slug 确定性推导（uuid5(NS, topic:<slug>)）；DB 存量 4 话题全 closed 零写路径 | `cli/commands/fs.py`、`sdk/python/map_fs/parser.py:275` | F6 价值评估：uuid5 前缀相较 slug 无增量价值（slug 更短且人类可读） |
| FS 待办是即时投影非持久行：`fs_topic_progress_for_agent` 每请求 derive_work 推导（文件存在性），`idempotency_key=fs:<kind>:<slug>:round<N>` 仅作去重标识；清理 = 写 round 文件 / host 推轮 | `server/services/fs_source_service.py:187-243` | F7：无 Notification 行可 dismiss；DB 已读存储 = 第二套状态，违背 FS 事实源原则 |
| CLI dismiss/read/mark-seen 对 fs 目标走 `_fs_projection_noop`（提示 + no-op），DB 目标正常调用 | `cli/commands/topic.py:653-713` | F7 现状已是 no-op + 提示；结论 = 维持现状 |

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M59a-1 基线测试 | 新增 `test_fs_scan_plane_perf_baseline`（slow 标记）：对仓库真实 `map/` 平面跑 50 次迭代 scan_plane，捕获 p50/p95/p99 + 话题/实验计数，写 `.map/perf-baselines/fs-scan-plane-baseline.json`（schema 对齐 phase1：phase/iterations/percentiles/captured_at/note + counts）；断言 p95 < 1.0s 防退化静默 | `tests/test_fs_scan_plane_perf_baseline.py` |
| M59a-2 触发条件注释 | `scan_plane` docstring 补：全量扫描为有意设计；触发条件（话题 >500 或 p95 >100ms）满足时先复测基线再评估 mtime 增量 | `sdk/python/map_fs/parser.py:365` |
| M59b-1 lint 自动修复 | `ruff check --fix alembic/ test_project/`（UP007 Union→`\|`、UP035 deprecated-import、I001 import 排序） | `alembic/`、`test_project/` |
| M59b-2 手工修复 | SIM105 ×1：`try/except/pass` → `contextlib.suppress` | `alembic/`（定位后改） |
| M59b-3 gate 扩展 | ci.yml + nightly.yml 的 ruff check 行补 `alembic test_project` | `.github/workflows/` |
| M59b-4 回归锁定 | 新增 `test_lint_gate_coverage`：断言两个 workflow 文件的 ruff check 行含 `alembic` 与 `test_project` token（防再滑出） | `tests/test_lint_gate_coverage.py` |
| M59c PRD 回写 | F6（不做，依据 a-d）/ F7（维持 no-op + 提示，依据 a-c）已决标注写入证据清单两行 + 风险表 F6 行；M59 章节执行状态标注（完成后） | `docs/prd/v0.13.md` |

## 实现顺序

1. M59a 基线（测试 + 产物 + 注释）→ 跑一次生成基线文件
2. M59b lint（--fix → 手工 SIM105 → 验证 `alembic upgrade head` → gate 行 → 锁定测试）
3. fast-gate 全量回归
4. M59c PRD 回写 + 执行状态标注
5. 窄 commit + 收尾

## 风险与对策

- **alembic UP007/UP035 批量改动触碰迁移文件**：改动仅类型标注与 import 形态，不动 revision 链与 DDL；改后必跑 `alembic upgrade head`（dev 库已在 head 046，幂等验证）+ fast-gate（test_migrations 类测试兜底）
- **基线测试机器噪声**：与 phase1 同口径（多次迭代分位数 + note 标注环境）；断言阈值放宽至 1.0s（防 flaky，真实触发条件 100ms 写在注释不在断言）
- **scan 平面大小随仓库演化**：基线 note 记录 counts 快照；复测时 diff counts 判断是否规模驱动
- **F6/F7 结论被评审推翻**：结论附完整依据链（调研表）；若评审要求实施，范围膨胀归后续版本（PRD 既定：实施归 v0.14+ 里程碑）

## 开放问题

1. 基线测试默认 slow 标记（不进 fast-gate）——若评审要求进 fast-gate，迭代次数可降至 20（预计 <2s）
2. F6 结论「不做」是否需要同步回 M54 风险表原始行——本 plan 倾向只在 v0.13 PRD 标注（跨版本回写面大、收益低），留评审确认
