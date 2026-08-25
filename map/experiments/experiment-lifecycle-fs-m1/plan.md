---
title: "M1：实验生命周期 FS 事实源——index.md 契约 + 验证型写闭环"
acceptance:
  - "A1 index.md 契约：新建实验在 map/experiments/<slug>/ 落 index.md，frontmatter 含 phase / current_plan_version / creator / executor / topic / updated_at；评审条目不进 index，走 reviews/*.yaml（本切片可先落目录约定 + 写回最小 reviews 文件，完整 item 状态机可在 M2 补齐）"
  - "A2 验证型写：approve / start / complete / accept-result / plan revise 仍打 API；校验 token、允许边、creator/reviewer 门禁后回写 index.md。绕过 CLI 手改 phase（如改成 done）被 validate 拒绝：写回前校验 + 写后回读，非法文件不落盘"
  - "A3 新建不再以 DB experiments 行当主存储：create 后 map experiment show --id <slug> 读 index.md；DB 至多投影。若第一刀必须暂留投影行，plan 须写明退役条件且 show/list 以 FS 为准"
  - "A4 complete 全链路可 diff：一次 complete 使 git 可见 phase 变更 + current_plan_version 推进 + reviews/ 落盘，不能只改库里的 phase"
  - "A5 锁与相位解耦：start 成功后 running 锁与心跳仍在 DB；同一实验不能并发 start 两次（DB 幂等锁，与 index.md 正文无关）；experiment.lock.no_progress 语义与 FS 化前一致"
  - "A6 id 路由：map experiment --id 接受 slug / uuid5(experiment:<slug>) / 存量 DB uuid（uuid→DB 优先、404 后 FS 反查；slug→FS 优先），对标话题 M51/M56"
  - "A7 start --executor participant：本实验由 host 创建并提交评审，approve 后 start --executor participant；participant 执行，host 保留 cancel/withdraw"
  - "测试面：非法手改 phase 被拒、合法 complete 回写 index.md、锁并发、id 三态路由单测全绿；ruff check 通过"
evidence_keys:
  - "实测：手改 index.md phase=done 再走 validate/commit → 拒绝报错原文（A2）"
  - "实测：合法 complete 后 git diff 含 index.md phase 与 reviews/（A4）"
  - "实测：start 两次 → 第二次被 DB 锁拦截（A5）"
  - "实测：map experiment show --id <slug> 与 --id <uuid5> 读到同一实验（A6）"
  - "pytest_summary：本实验新增/改动测试 passed、failed=0"
  - "ruff: ruff check . 全绿"
dependencies:
  - "话题 experiment-lifecycle-fs-source Round 2 Summary：M1 验证型写闭环；M2 存量迁移；M3 derive 展示层（waker 仍用 DB inbox）"
  - "先例：话题 M58 验证型写（API 校验 → 本地写回 index.md）"
  - "非目标：不搬 MAP_DATABASE_URL；token/wakeable/锁不改纯文件；不做 M2 94 条存量迁移；不把 derive_work 换成唯一 waker 触发源"
---

# M1：实验生命周期 FS 事实源——契约 + 验证型写闭环

## 背景

话题 `experiment-lifecycle-fs-source`：实验正文已在 `map/experiments/`，相位与评审条目仍在 SQLite。`FsExperiment` 能读 `index.md` 的 `phase`，写路径未接状态机。要降低对业务库的依赖，但不能变成「改 markdown 就过门」。

本切片只做 **M1**。M2（存量全量落盘 + `sync --check`）、M3（todos 从 index 派生展示）另开实验。

## 定稿决议（Round 2）

| # | 决议 |
|---|------|
| D1 | 验证型写：API 真拦截，`index.md` 是门禁通过后的回写镜像 |
| D2 | `index.md` 只放不变量；评审条目 `reviews/`；不冗余 `open_unreasonable_count` |
| D3 | 相位权威在 FS；running 锁 / no_progress / 通知 inbox 仍在 DB |
| D4 | waker **触发**仍用 DB wakeable；FS derive 只做展示（M3） |
| D5 | 第一版不按 standard/direct 拆目录；边表不缩 |
| D6 | 执行：`--executor participant`（M1+M2） |

## 实施顺序

1. **I0** 盘点：`map experiment *` 写路径、`FsExperiment`/`write` API、现有 `experiments.phase` 调用点
2. **I1** `index.md` 契约 + parser/writer（A1）；create 写目录
3. **I2** 生命周期命令改验证型写回 index.md（A2、A4）；非法手改拒绝
4. **I3** show/list/id 路由以 FS 为准，DB 降为投影（A3、A6）
5. **I4** 锁保持 DB（A5）+ 单测与一条 live 动线

## 风险

- A3 若第一刀无法去掉 INSERT：须在 log 写明投影行语义，不得让 DB phase 与 index 双权威
- 同机 vs 远程：validate 通过才落盘；远程走现有 FS CAS
- 执行中 revise：breaking 门禁（bd9b21f6）仍有效，回写的是 index.md 的 phase
- `waive_reason` 未进 `derive_work`：已知现象，归 M3，本切片不修

## 非目标

M2 存量 94 实验迁移；M3 替换 waker 判定源；取消平台裁判；锁/token 仓库化；搬数据库路径。
