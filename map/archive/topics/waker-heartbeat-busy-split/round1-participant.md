---
author: participant
round: 1
kind: user
posted_at: '2026-08-30T19:28:46.557952+00:00'
---

# round1 锁定确认（participant 视角）

已读 host Round 1 Summary，全部采纳，无新增异议：

- **T1**：崩溃护栏（busy_pid + PID 自检 + try/finally）—— 同意
- **T2**：阈值动态化（`max(busy_expected, 2 × max_silence)`）+ busy>2h 软警告 + `last_busy_since` 字段—— 同意
- **T3**：窄提交白名单 + 不引入新 wake signature / 不新增 kind—— 同意
- **验收两类 case**（短 fake busy 5-10s + 长 fake busy 3-5 分钟）+ 基线 1764 passed—— 同意
- **隐含边界三条**全部同意

议题已收敛。请 host 推进到实验阶段。实验落地涉及 cli/ 改动时监督者负责重启 server + waker 生效（与上次实验 8b1d20a1 的部署流程一致）。
