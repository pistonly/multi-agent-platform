---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：运维可见性四件套采纳 participant 优先级 ④②→①→③

## 定稿

1. **④ audit CLI 出口 排第一**（收益/成本比最高，纯读、三方受益）：`map topic history --id <slug>` 与 `map audit list --target <id>`，覆盖 target_id 反查（topic/experiment）、按 agent 过滤、时间窗过滤——终结「复盘靠手写 SQL」。
2. **② 管道停滞平台化 排第二**：停滞判定进 server（/status 或 fs_plane_status 的 **stale-ready 计数 + 最长时长**）；保守阈值——只对「ready 且无实验在跑」告警，避免正常等待误报（participant 边界 2）。
3. **① pid 检测** 与 ② 同批：pid 文件失真 → 端口探活优先，影响 ops 单点不放大协作成本。
4. **③ remote 分叉监控 降级为附属**（不独立进 server 实时告警）：作为 ② 的附属，status 给 ahead/behind 数值列、人工看即可；低频率脚本检查 + 发布前提醒，轮询告警不单做（误报风险 > 收益，采纳 participant 持保留）。

## 动线

- 开实验落地：主 ④+②（可先行），①附带，③仅 status 数值列。
- 验收：④ 各跑通一条真实记录（history/audit）；② 造 ready 超时 FS 话题看到 stale-ready 计数与最长时长。
