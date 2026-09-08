---
author: participant
round: 1
kind: user
posted_at: '2026-09-04T17:05:43.309840+00:00'
---

**立场**：支持 FS-first、先对账再切换写入，但“停止 DB INSERT”的门槛不能只定义为一次 `sync --check` 零 diff。应先把 DB 明确降为 projection：`index.md` 权威管理 phase、title/description、creator/executor/topic、plan version 与时间字段；`plan.md`、`log.md`、`reviews/*.yaml` 分别权威管理内容。DB 只保留稳定映射、权限/锁、review item、审计、通知、transition receipt/token 等运行时投影。任何 active phase 在没有这些 projection 能力或显式 lazy materialization 前，不应停止主行 INSERT；FS-only 历史终态可以只读展示并明确 `no_db_projection`，不能被误当作可执行实验。

**理由 / 风险**：

1. 当前 `cli/experiment_fs.py::experiment_sync_check` 实际只比较 `phase`、`current_plan_version`、`projection_id`，并检查目录是否存在；没有比较 title/description/creator/executor/topic，也没有对 plan/log/review 做规范化 hash。`fs_only` 甚至可以是合法历史状态，而 `db_only`/孤儿 projection 的策略也未在 `ok` 判定中清楚区分。建议输出固定分类：`aligned`、`fs_only_terminal`、`db_only`、`divergent`、`invalid`，并让 active 的 `fs_only`、缺失目录、不可解析文件一律阻塞切换；正文只输出对象和字段/hash 摘要，不打印全文。

2. 切换应有按 project 的 feature flag 和 kill switch，顺序为：只读对账/告警 → 双写观察期 → 新建路径按 flag 停止重复 INSERT → 旧 active 实验继续兼容 → 稳定后才扩大范围。指标至少记录 drift 分类、projection 缺失、CAS 冲突、同步延迟与恢复成功率；不能因一段时间恰好零 diff 就永久关闭回滚通道。

3. 远程 `projection-cache` 继续采用单 publisher + revision CAS：客户端先取 revision/hash，变更集显式携带 upsert/delete tombstone 和 `result_content_hash`；旧 revision 必须 409，不能 last-writer-wins，也不能用“payload 未出现”隐式删除。失败时保留本地文件和 last-known-good projection，给出 `sync diff/publish` 修复路径。topic、experiment、work 的读面要返回同一 source revision 和 stale 原因。

**建议验收**：

- 对账单测覆盖元数据/plan/log/review hash、FS-only 终态与 active、DB-only、缺目录、非法 phase、projection_id 过期，以及重复运行零变更幂等。
- 远程 e2e 覆盖两客户端同一 base revision 仅一个 CAS 成功、乱序/重复 delta、显式 tombstone、断网后恢复、publisher 越权、content_root 不一致和 last-known-good 保留。
- 切换回归必须证明：关闭 INSERT 后 active 生命周期仍有锁、评审、审计、通知和权限闭环；若做不到，开关只能停“重复内容写入”，不能停运行时 projection 主行。
