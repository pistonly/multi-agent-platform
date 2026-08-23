---
title: "M62 废弃 platform feedback 全链路（清账 + 拆除 + stub，v0.15）"
acceptance:
  - "M62-a 清账（时序硬约束：**全部完成并复核后才允许动 M62-b**，本条为 M62-b 显式前置条件）：DB `platform_feedback` 11 条全部 `status=resolved`（含最早的 1 条），每条 `metadata_json` 记核证证据（修复 commit / 测试锁定 / 退役依据）；操作走 host + admin token 的 CLI `feedback update`（不碰 DB 文件）；处置表（id / 摘要 / 处置 / 证据）落实验日志；转 GitHub issue 分支经 participant 核证为空集（10/10 已修或已随 M58 退役消亡），若执行中发现反例按 reviewer 三分类判据补 issue 并回写 URL"
  - "M62-b CLI：`cli/commands/feedback.py` 4 命令（submit/list/get/update）全部改为 `_DB_WRITE_RETIRED` exit 2 引导 stub，文案两行分流（bug → GitHub issue 附 repo 链接；改进想法 → host 开 MAP 话题）+ 一句「历史 feedback 数据只读保留」；`map feedback --help` 与 `map --help` 无死链"
  - "M62-b API：`server/api/feedback.py` 4 端点（POST + admin GET×2 / PATCH 同拆）返回 410 + 引导 body；`server/main.py:229` 挂载移除；`platform_feedback` 表与 11 条数据不删（只读保留，M58 先例）"
  - "M62-b SDK：`sdk/python/map_client/client.py:1150-1211` 5 方法（submit_feedback / list_feedback / list_feedback_page / get_feedback / update_feedback）全删无 stub；CHANGELOG 标 breaking（0.x minor 允许）；`build/lib/map_client/` 随 rebuild 自动更新不手工改"
  - "M62-b Web：`web/src/pages/FeedbackPage.tsx`、`web/src/App.tsx:52` 路由、`web/src/api/client.ts:598-623` 三 fetcher、`web/src/api/client.test.ts:146+`、`web/src/api/types.generated.ts` 对应类型全拆；vitest 构建与测试通过，无死页死链"
  - "M62-c Skill 三层同步：`.cursor/skills/map-project-collab/references/platform-feedback.md` 删除或改引导 + **`cli/skills/`（随包分发源）同步** + 各 persona runtime home 副本（重装或同步，验收点名核证）；`map-project-collab` SKILL.md 的 feedback 引用清理；README / QUICKSTART 无 feedback 入口残留"
  - "测试：`tests/test_feedback.py` / `tests/test_feedback_admin_cli.py` 整文件删除（两文件本不在 `_FAST_GATE_MODULES` 白名单，CI 未跑过，删除对 CI 零影响）；`tests/cli/test_dry_run_write_commands.py` 移除 feedback_app 相关登记；`tests/cli/test_compat.py` 快照同步；**stub 行为新测试显式加入 `_FAST_GATE_MODULES` 白名单**（v0.14 R-c-① 硬验收同款）；全量 fast-gate 回归通过"
  - "防半拆验收（participant 建议）：拆除后 `grep -ri feedback`（排除 map/archive/git 数据与 CHANGELOG）仅剩 stub 命令定义、引导文案、只读历史层引用——无任何残留功能入口"
  - "PRD 回写：docs/prd/ 新增 v0.15.md（或并入现有结构），承载提案、评审定稿 1-6、participant 核证明细与 reviewer 四条废弃证据，状态 accepted + 实验链接"
evidence_keys:
  - "清账处置表（11 条 id/摘要/处置/证据）+ `SELECT status, COUNT(*)` 全 resolved 快照"
  - "CLI 实测：4 命令 stub exit 2 输出记录（含两行分流文案）"
  - "API 实测：4 端点 410 + 引导 body；server 启动无 feedback 路由"
  - "grep -ri feedback 防半拆输出（与验收口径对照）"
  - "web 构建与 vitest 通过输出；types regenerated diff"
  - "fast-gate 全量通过；stub 新测试在白名单内的 collect 证据"
  - "PRD v0.15 diff；Skill 三层删除/同步 diff"
dependencies:
  - "v0.15 话题评审通过（v015-feedback-deprecation-design round2 双方全票、争议 1-4 全闭合，host 已标 ready；定稿 1-6 见话题 round2-host.md）"
  - "M58 `_DB_WRITE_RETIRED` exit 2 引导模式先例（cli/commands/topic.py）"
  - "participant 10/10 核证结论（2026-08-23，round2-participant.md）：转 issue 空集，含 #7 `.env` token git 历史零提交、#11 双层修复（377863f + 97c6e5b）等逐条证据"
  - "reviewer 四条废弃独立证据（round2-reviewer.md）：零 notification 调用 / triage 协作 persona 零可见 / 单向消息结构性劣势 / 自托管回传经济学"
  - "清账需 admin token（MAP_ADMIN_TOKEN 或 ~/.map/admin.yaml）；feedback list/update 为 admin-only 入口"
---

# M62 废弃 platform feedback 全链路（v0.15）

## 目标

按话题 v015-feedback-deprecation-design round2 定稿执行：**先清账后拆除**（时序硬约束），把 `map feedback` 全链路（CLI / API / SDK / Web / Skill 三层）退役为 `_DB_WRITE_RETIRED` 引导 stub，DB 表只读保留。与 M58（DB 话题写退役）、M60/M61（归档收口）同属工程卫生清偿系列。

## 定稿依据（评审闭合，争议 1-4 全决）

| 争议 | 定稿 | 依据 |
|------|------|------|
| 1 废弃 vs 修复 | **废弃定案** | reviewer 四条独立证据（修复=重建 issue tracker 违反薄核心）；participant 补强（10/10 已修 0/10 关单，闭环记账从未发生） |
| 2 清账口径 | 三分类判据 → 实测全落「已修/已吸收」分支 | participant 逐条核证（10/10，转 issue 空集）；host 持 admin token CLI 操作 |
| 3 stub 边界 | admin triage 入口同拆（update 与只读承诺矛盾）；四命令统一 stub 文案 | reviewer 定稿 6 |
| 4 SDK 兼容 | 5 方法全删无 stub，CHANGELOG breaking | 双方一致；生产调用方仅同为拆除对象的 cli 命令层 |

## 实现顺序（时序硬约束内嵌）

1. **M62-a 清账**（host + admin token）：`feedback list --status new` 拉全 → 逐条按 participant 核证证据 `update --status resolved`（metadata_json 记证）→ 复核 `SELECT status, COUNT(*)` = 11 resolved → 处置表落日志。**复核通过前禁止任何 M62-b 动作。**
2. M62-b 后端：API 4 端点 410 + main.py 挂载移除 → SDK 5 方法删 + CHANGELOG → CLI 4 命令 stub 化
3. M62-b Web：页面 / 路由 / fetcher / types 拆除，vitest 绿
4. M62-c Skill 三层 + README/QUICKSTART
5. 测试：删两文件 + 白名单表/快照同步 + stub 新测试入 FAST_GATE 白名单 → 全量回归
6. PRD v0.15 回写

## 风险与边界

- **清账工具自断风险**：update 是拆除对象——顺序颠倒即失去清账能力（硬约束存在的原因）
- **runtime home 副本**：各 persona `.map/claude-runtime-home-*/.claude/skills/` 的旧副本靠重装收敛；旧 memory 中 feedback 指引自然失效，不专门清理（reviewer 核证不在范围）
- **外部 SDK 使用者**：量级≈0 且与「能开 issue 的群体」重合（participant 论证），breaking 风险接受
- **不做**：webhook 转发等修复方案（争议 1 已决）；不删 DB 表与数据
