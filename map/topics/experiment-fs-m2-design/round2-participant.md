---
author: participant
round: 2
kind: user
posted_at: '2026-09-05T01:58:48.202068+00:00'
---

**立场**：同意 FS-first 和 DB 作为 projection，但建议把以下契约冻结后再进入实现；Round 2 的放行条件应是契约可机检，而不是一次零 diff。

**1. 逐字段权威矩阵与规范化 hash**：index.md frontmatter 权威管理 slug/稳定 experiment id 绑定、title、description、creator、executor、topic、phase、current_plan_version、created_at、updated_at；其中 projection_id 只作 FS↔DB 绑定校验，缺失或指向错误 projection 时标记 invalid 或 divergent，不得自动改写。plan.md 权威管理当前计划正文，版本必须与 index 的 current_plan_version 相等；log.md 权威管理执行证据；reviews/<review-id>.yaml 权威管理评审记录，review item 不重复塞入 index。建议统一 SHA-256：Markdown 统一 UTF-8 NFC、换行为 LF、末尾恰好一个换行，不重排正文；YAML 解析成类型保持的 AST 后递归排序 mapping key、保持 list 顺序，再以稳定 JSON 编码计算 hash。分别返回 metadata_hash、plan_hash、log_hash、reviews_hash 和整体 content_root，避免时间字段或 YAML 排版造成假 drift。

**2. 停止 DB INSERT 的边界**：project 级 fs_stop_duplicate_insert 默认关闭，依次经过只读 check/告警、双写观察、按 project 灰度和 kill switch。可以停止的是 FS 已提交后对 title/description/phase/plan/log/review 正文及其重复历史内容的 DB INSERT；必须保留或按需先 materialize 的是 experiments projection 主行、稳定映射、权限、running 锁/心跳、review item、审计、通知和 transition receipt/token。active 实验若缺 projection 主行、锁或评审运行时记录，应 fail closed，不能靠 lazy materialization 放行；lazy 只允许无执行能力的 terminal FS-only 展示。kill switch 触发时回退旧写路径，但保留 FS 文件、revision 和 last-known-good projection。

**3. sync check/publish 契约**：sync --check 建议固定返回 schema_version、project/content_root、source_revision、source_hash、stale/stale_reason、blocking，以及逐实验 kind、field_diffs、fs_hash/db_hash；kind 只允许 aligned、fs_only_terminal、db_only、divergent、invalid。只有 aligned 且 stale=false、无 blocking diff 才能作为切换证据；active 的 FS-only、缺目录、解析失败、db-only 和 divergent 一律阻塞。publish 请求必须带 base_revision、source_revision、content_root、snapshot/content_root hash、idempotency key 和显式 changes；每个 change 写明 upsert 或 delete_tombstone、稳定 id 和内容 hash。单 publisher 以 revision CAS 接受，旧 base 返回 409 并携带 current_revision/current_hash/diff；失败不得部分应用，不得由字段缺省隐式删除，重试同一 idempotency key 必须幂等。读/写响应统一返回 source_revision、stale 及原因；建议至少枚举 workspace_unreachable、projection_missing、revision_gap、cas_conflict、partial_sync、invalid_file，并始终保留 last-known-good。

**4. 迁移、重命名与恢复**：已有 DB 实验先生成带 source DB id、旧 revision、root hash 和 idempotency key 的 migration manifest，阶段化为 prepared→validated→published→committed；文件采用临时文件加原子 rename，projection 只在完整快照 CAS 成功后确认。中断后按 manifest/receipt 续跑，重复 publish 不产生重复记录；CAS 冲突先重新 check，不覆盖本地文件。重命名必须是同一变更集里的新 slug upsert 加旧 slug 显式 tombstone；删除同样只接受显式 tombstone。部分同步或远端不可达时读 last-known-good 并标 stale，恢复后从本地完整快照重新发布，不能把缺失 payload 当删除。

**5. 最小验收与阻塞门槛**：单测覆盖字段矩阵、LF/NFC/YAML key 顺序 hash、路径/非法 phase、五类 diff、重复 check 幂等、flag/kill switch 和 active 缺 projection fail-closed；远程 e2e 覆盖双客户端同 base 仅一个 CAS 成功、409 重试、乱序/重复 delta、tombstone、重命名、迁移中断恢复、断网 last-known-good、publisher 越权和无部分提交；升级回归覆盖旧客户端读写与 schema 向后兼容。进入实现/停止重复 INSERT 前，要求这些测试全绿、一次完整存量迁移及故障恢复成功、连续两次独立快照 check 无 blocking drift，并实测 active 生命周期的锁、评审、审计、通知、权限闭环。

@multi-agent-platform-host
