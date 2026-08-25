---
title: "运维可见性第一批（④ audit CLI 双入口）：map audit list --target + map topic history"
acceptance:
  - "C1 `map audit list --target <slug>`：--target 接受话题 slug/uuid5 或实验 slug/uuid（自动识别 target_type），走服务端既有 `GET /audit`（target_type+target_id，**非 admin**，权限由 perm.ensure_audit_target_access 把关）；可与既有 --kind 过滤组合；这是 close_note「top-level slug 双入口」的第一入口——顶层不用先知道 type"
  - "C2 `map topic history --id <slug>`：话题维度时间线（第二入口）——以 `GET /audit?target_type=topic&target_id=<uuid5>` 为主轴，并入关联实验（experiment.topic_id 匹配该话题）的 audit 事件，按时间倒序统一排序输出；host/participant 均可调用（同一权限校验，不开新权限面）"
  - "C3 输出形态：时间倒序表（时间/动作/actor/摘要），`--format table|yaml|json` 遵循 v0.12 M54A 命令级格式覆盖约定；空结果输出友好提示而非空表头"
  - "C4 权限边界不动：`GET /admin/audit` 全局端点与 admin-only 语义零改动；CLI admin 全局路径（`map audit list` 无 --target）行为不变"
  - "C5 本批只做④：close_note 排定的② 管道停滞平台化、① pid 探活、③ remote 分叉（降级为②附属）留待后续实验，不在本实验范围（记入 dependencies，不算本实验 debt）"
  - "测试面：--target 解析（话题/实验 slug 与 uuid 双向、错 slug 报错友好）、topic history 聚合排序（话题事件+实验事件交错）、权限拒绝路径、三种格式输出、空结果 五组新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "pytest_summary:五组新增单测全绿(pytest_summary)"
  - "实测输出:`map audit list --target <真实实验 slug>` 与 `map topic history --id <真实话题 slug>` 各一次成功输出落档(C1+C2)"
  - "grep 核证:cli/commands/audit.py 出现 --target 选项与 help;topic history 子命令存在;admin 端点零改动(C4)"
dependencies:
  - "话题 ops-visibility-batch（58c5ccdd-3cd5-58ec-b36e-16b2afc9e235）close_note 口径：④ audit CLI 出口排第一（topic history + audit list --target 双入口），附补充「audit list --target 接受 top-level slug 双入口」；②①③ 后续批次"
  - "服务端能力已就绪：`GET /audit`（server/api/audit.py，target 级、非 admin、ensure_audit_target_access）与 audit_service.query_by_target/query_all 均已存在——本实验纯 CLI 层补口，不动 server"
  - "CLI 现状：cli/commands/audit.py 仅 `map audit list`（admin 全局，--kind/--experiment 过滤），无 --target；`map topic history` 不存在"
  - "slug→(type,id) 解析可复用 CLI 既有 topic slug/uuid5 路由与实验 slug/shortid 解析设施（cli/shortid.py、T2-P1/T2-P2 约定）"
  - "与本批 fs-write-entry-validation（sdk/map_fs 写路径+parser）、experiment-done-topic-close-event（server phase/notification）无代码冲突（本实验改 cli/commands/audit.py + cli/commands/topic.py）"
---

# 运维可见性第一批（④）：audit CLI 双入口

## 背景

话题 `ops-visibility-batch` 四件套中④排第一：audit 数据只在 DB 与 admin 全局 CLI 里，普通 agent 无话题/实验维度出口——排查「这个话题/实验发生过什么」只能翻日志文件或找 admin。服务端 `GET /audit`（target 级、非 admin）与 `query_by_target` 早已存在，缺的只是 CLI 双入口。

## 定稿决议（close_note 口径）

| # | 决议 | 来源 |
|---|------|------|
| D1 | ④ audit CLI 出口第一优先级：topic history + audit list --target 双入口 | close_note decision |
| D2 | audit list --target 接受 top-level slug 双入口（话题/实验 slug 或 uuid 自动识别） | close_note rationale 附补充 |
| D3 | ②管道停滞平台化第二（保守阈值）、①pid 探活与②同批、③remote 分叉降级为②附属——均不在本批 | close_note decision |

## 实施顺序

1. **I1 slug 解析器**：`--target` 参数解析（话题 slug/uuid5 ↔ 实验 slug/uuid/shortid → target_type+target_id），复用既有路由设施 + 单测
2. **I2 audit list --target**（C1+C3+C4）：CLI 命令扩展，接 `GET /audit`，格式输出 + 单测
3. **I3 topic history**（C2+C3）：`map topic history --id <slug>` 聚合话题事件+关联实验事件 + 单测
4. **I4 实测 evidence**：对本仓库真实话题/实验各跑一次成功输出落档

## 风险与边界

- ensure_audit_target_access 的可见性语义以实现时核对为准（plan 不预设放宽/收紧）；若实验 slug 与话题 slug 撞名（理论可能），解析顺序定实验优先并输出歧义报错列出两者——不静默猜
- topic history 并入实验事件的查询量：单话题关联实验数有限（当前最多 2），不做分页扩界；--limit 透传服务端上限（≤200）
- `map audit list` 既有 admin 行为零回归（C4 负向单测）
