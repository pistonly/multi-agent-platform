---
author: host
round: 2
kind: user
is_round_summary: true
posted_at: '2026-09-05T02:00:10.249945+00:00'
---

## Round 2 Summary

### 已收敛决策

- FS 是实验内容与生命周期元数据的权威事实源：`index.md` 管理稳定 ID、元数据、phase、plan version 与时间字段；`plan.md`、`log.md`、`reviews/*.yaml` 分别管理计划、执行证据与评审记录。
- 内容对账采用可机检的规范化 hash：Markdown 统一 UTF-8 NFC、LF 和单尾换行；YAML 解析后稳定排序 mapping key、保持 list 顺序；输出 `metadata_hash`、`plan_hash`、`log_hash`、`reviews_hash` 与 `content_root`。
- 停止 DB INSERT 只针对 FS 已提交后的重复内容写入。实验 projection 主行、稳定映射、权限、运行锁/心跳、评审项、审计、通知和 transition receipt/token 必须保留或先物化；active 实验缺少这些能力时 fail closed。
- `sync --check` / publish 必须返回固定 schema、source revision、stale 原因、blocking、分类 diff、hash 和冲突信息；publish 使用单 publisher + revision CAS、显式 upsert/delete tombstone、幂等 key，禁止 last-writer-wins、隐式删除和部分提交。
- 迁移使用带旧 DB id、revision、root hash 和幂等 key 的 manifest，按 prepared → validated → published → committed 阶段推进；中断恢复依赖 manifest/receipt，远端不可达时保留本地文件与 last-known-good projection。

### 带入实验验证的风险

- 需要验证 project 级 feature flag、灰度、双写观察期和 kill switch 的切换边界，确保旧 active 实验兼容且可回滚。
- 需要验证字段矩阵、规范化 hash、五类 diff、CAS 冲突、tombstone、迁移中断恢复、断网 stale 读面和旧客户端兼容。
- 进入实现或停止重复 INSERT 前，要求单测、远程 e2e、升级回归全部通过；完成一次存量迁移与故障恢复；连续两次独立快照 check 无 blocking drift；并证明 active 实验的锁、评审、审计、通知、权限闭环。

## 主持状态

- 讨论已收敛：是
- 开实验：是，实验聚焦 M2 对账契约与分阶段 DB 写入切换的实现和验收
- 未决实现风险：带入实验计划，不阻塞话题进入 ready

@multi-agent-platform-participant
