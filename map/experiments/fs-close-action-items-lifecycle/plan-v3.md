---
title: "FS close_note 的 action_items 解析入跟踪:断开两年半的执行项闭环"
acceptance:
  - "A1 action-items.yaml 事实源:话题文件夹 `action-items.yaml`(frontmatter 无需,列表文档)定义 action_items(owner/title/status open|done|cancelled/evidence/created_at);map_fs parser 解析入 FsTopic(ack 同源模式),收敛时 host 落盘(手写或 `map topic action-item add`)"
  - "A2 收敛即入义务:server `fs_topic_progress_for_agent` 把 open action_items 投影为 obligation work items(kind=action_items,与 stale_open_topics nudge 同构),owner 精确路由(owner persona 可见,他人不可见)——waker 零新逻辑,经既有 work_items 通道唤醒"
  - "A3 close 门禁(唯一防线):`validate_fs_close` 校验话题 action-items.yaml 无 `status: open` 项才放行 close;有 → 409 带各项 title/owner;done/cancelled(带 evidence 或 cancel 理由)不阻——invariant:closed = 零尾款,只有 open 话题才有未完成项"
  - "A4 complete 带证据:`map topic action-item complete --topic <slug> --id <n> --evidence <commit/pytest/文件路径>` 写回 yaml(status: done + evidence 行);evidence 必填,空证据拒绝——不许自说自话"
  - "A5 cancel 语义:`map topic action-item cancel --reason <...>` 写回 status: cancelled + reason;cancel 不挡 close(显式放弃,审计留痕)"
  - "A6 存量处置:已 close 话题不回填、不重解析 close_note(closed 应零尾款;merge-github-main-into-local 的 merge 已由用户执行 6aaca4c,git 历史即证据,不造追补记录);close_note 中 action_items 文本段约定废弃,Skill 文档(host-checklist)改为引导收敛时写 action-items.yaml"
  - "A7 投影缓存兼容:remote 部署下 action-items.yaml 随 map fs push 进投影缓存,fs_topic_progress_for_agent 的 projection 回退分支同样投影(读路径同源)"
  - "测试面:yaml 解析/投影路由(owner 归属)/close 门禁(409 与放行)/complete 拒空证据/cancel/投影缓存回退的新增单测全绿;fs_source / topic close / todo / waker 既有测试无回归;ruff check 通过"
evidence_keys:
  - "pytest_summary:新增单测(A1-A7 对应)全绿 + 既有套件无回归"
  - "实测输出:演示话题(本实验自带 mini fixture)收敛落盘 action-items.yaml → owner work 快照出现 kind=action_items → complete 带证据 → close 门禁放行,全链走通"
  - "grep 核证:validate_fs_close 含 open 项 409 分支;fs_topic_progress_for_agent 含 action_items 投影;host-checklist.md 已改引导收敛时落盘"
dependencies:
  - "主话题 fs-close-action-items-lifecycle(281de0d5-ea93-536f-89ef-93e0870b5c26)Round 2 Summary 定稿(方案 2 为主 + 方案 1 辅助)"
  - "**plan v3 变更来源:用户对话定案(2026-08-24,非 reviewer 项)**——时序修正:action_items 在话题收敛时结构化落盘(FS 事实源),不是 close 时解析 close_note;close 门禁从辅助升级为唯一防线;closed = 零尾款。原 v2 的『close 时解析 close_note 文本 → DB TopicActionItem 建行 + alembic 051 弃 FK』路线随之退役(见 D6/D7 取舍)"
  - "前置:4b1192cc advance-round ack 合规校验已落地(action-items.yaml 解析复用其 parser 模式与 actionable error 渲染);stale_open_topics nudge(fs_topic_progress_for_agent)为 FS 投影义务同构先例"
  - "活标本:merge-github-main-into-local(103d6482)close_note action_items 为旧模式遗存,merge 已执行(6aaca4c),按 A6 只改文档不回填"
---

# FS close_note 的 action_items 解析入跟踪:断开两年半的执行项闭环

## 背景

话题 `merge-github-main-into-local`(103d6482)于 08-23 close,close_note 写明 action_items「host 执行 merge……」,但 merge 一度未执行(后由用户手动完成)。用户实证指出:话题没执行操作就关闭不合理,并进一步定案时序修正(plan v3):**action_items 应在讨论收敛时就结构化确定,执行完才 close;话题关了还留着 action_item 的模型本身不合理**。

根因是机制断链(非执行者过失):

1. **DB 时代 action_items 全链机制健在**:`topic_resolve_service.py:175` 建 TopicActionItem → todos action_items 桶 → waker 升级 → complete 清除。
2. **FS 路径(v0.13 起)绕开了这一切**:close_note 纯文本,无解析、无跟踪。
3. **close 后义务真空**:stale nudge 只对 open 话题 → close_note 执行项无人推动。
4. **时序错误(v3 新认定)**:close 语义是「解决了」,但 action_items 没做就 close = 宣布完成而事实未完成;为此造「close 后跟踪系统」是在给错误的时序打补丁。

## 定稿决议(plan v3,用户对话定案修订)

| # | 决议 | 说明 |
|---|------|------|
| D1 | **时序修正(核心)**:action_items 结构化时点 = 话题收敛时(Round Summary / ready 时),不是 close 时 | 载体 = 话题文件夹 `action-items.yaml`;讨论定案即落,执行期被催,清零才 close |
| D2 | **close 门禁 = 唯一防线**:action-items.yaml 存在 `status: open` 项 → 409 | v2 中门禁是辅助、解析建行是主;v3 反转——closed = 零尾款 invariant |
| D3 | **complete 必须带证据**:commit hash / pytest 摘要 / 文件路径,写回 yaml | 不许自说自话;cancel 显式带理由,不挡 close |
| D4 | **载体 = FS 投影义务,零 DB 建行**:fs_topic_progress_for_agent 把 open 项投影为 obligation work items(kind=action_items),与 stale_open_topics nudge 同构 | waker 走既有 work_items 通道,零 schema 迁移;不碰 TopicActionItem 表 |
| D5 | **存量不回填**:已 close 话题零尾款是 invariant,追补记录反而破坏语义;close_note 文本 action_items 约定废弃,Skill 引导改收敛时落盘 | merge 案例的证据已在 git(6aaca4c) |
| D6 | **v2 DB 路线(I0 alembic 051 弃 FK / D5 桥接建行)退役**:该路线解决的是「close 时 DB 建行被 FK 阻断」,v3 不建行则 FK 问题不复存在 | 0641c52c 核证事实保留在调研表(若未来恢复 DB 路线仍适用);避免一次 schema 迁移 + DB/FS 双源 |
| D7 | **与实验的互斥**:action_item 挂了实验的项不再进 action-items.yaml(实验 my_open_experiments/pending_reviews 义务已覆盖,防双催) | 四门 Rubric 满足的走实验,轻量执行项走 action_item,纪律/约定写结论正文 |

## 调研事实(已核证)

| 事实 | 位置 |
|------|------|
| FS 投影义务同构先例:stale_open_topics nudge 在 fs_topic_progress_for_agent 生成 obligation work items,waker 经 work_items 通道唤醒(已 dogfood 验证) | `server/services/fs_source_service.py`(fs_topic_progress_for_agent) |
| FS 话题解析器模式:frontmatter + 文件夹解析,ack 合规校验同源 | `sdk/python/map_fs/parser.py`(4b1192cc 落地) |
| FS close 验证型写:validate → 本地写回 → commit,409 形态已有先例(ack 未满) | `validate_fs_close` `server/services/fs_source_service.py`;`validated_write_flow` `cli/commands/fs.py` |
| close 门禁现状只挡活跃实验,不看执行项 | `server/services/topic_lifecycle_service.py:48-54` |
| (DB 路线备查)TopicActionItem.decision_id/topic_id 硬 FK、FS 话题无 DB 行、046 弃 FK 先例 | `server/domain/models.py:443/445`;`alembic/versions/046_experiment_topic_fk_retirement.py` |
| Skill 层 close_note action_items 模板(约定,系统零解析)——A6 改造对象 | `.cursor/skills/topic-host/references/host-checklist.md:73-76` |

## 实施顺序(plan v3,评审可调)

1. **I1 yaml schema + parser(A1)**:map_fs 定义 action-items.yaml 格式(owner/title/status/evidence/reason/created_at)与解析(FsTopic.action_items 字段);格式错漏给 actionable error(复用 4b1192cc 渲染)。
2. **I2 投影义务(A2)**:fs_topic_progress_for_agent 对 open 话题的 open action_items 生成 kind=action_items obligation 项(idempotency_key fs:action_item:<slug>:<idx>;owner persona 精确路由);projection 回退分支同源(A7)。
3. **I3 close 门禁(A3)**:validate_fs_close 读 yaml,存在 open 项 → 409(title/owner 列表 + 引导 complete/cancel);全 done/cancelled 放行;无 yaml 话题不受扰。
4. **I4 CLI complete/cancel(A4/A5)**:`map topic action-item complete --topic <slug> --id <n> --evidence ...`(空证据拒绝)/ `cancel --reason ...`(写回 yaml;validated write 或本地写 + 约定)。收敛落盘入口:`map topic action-item add`(可选,I5)。
5. **I5 Skill 文档(A6)**:host-checklist.md close_note 模板的 action_items 段改为「收敛时写 action-items.yaml;close 时门禁校验清零」;topic-host SKILL.md 同步;wake.md kind 表加 action_items 行。
6. **I6 验证收尾(全部)**:A1-A7 单测 + mini fixture 全链演示(收敛落盘 → owner 快照见义务 → complete 带证据 → close 放行)+ 回归 + ruff。

## 风险与边界

- **owner 路由依赖 persona 短名**:yaml owner 写 persona 短名(host/participant),投影按 persona 匹配;未知 owner 解析时 error 不静默。
- remote(pure token)模式:action-items.yaml 随 map fs push 进投影缓存(读路径已同源),写回(complete/cancel)走 validated write 或本地写 + sync,与 round 文件同模式。
- 旧 close_note 带 action_items 文本的存量话题:A6 明确不解析不回填(否则又制造「closed 却有 pending」);Skill 文档负责拦截新写法。
- 门禁 409 对「决策型 close 无执行项」零影响(无 yaml / 全 done 即放行)。
- 工作树存在其他并行未提交改动,本实验只 stage 自身文件。

## 存量 merge 处置(独立于本实验代码交付)

- merge-github-main-into-local 的 merge 已由用户手动执行(`6aaca4c`,behind=0,push github 亦由用户完成);按 A6/D5 不回填、不造追补记录——git 历史即完成证据。
