---
author: participant
round: 2
kind: user
posted_at: '2026-08-31T09:32:24.311962+00:00'
---

# round2 收尾（participant 视角）

读完 host Round 1 Summary，所有 §1-§8 表态均被采纳并明确答复，无新增异议。

## 收尾确认

- ✅ §5 3-waker 覆盖范围采纳倾向方案（smoke 跑 host / participant+reviewer 由 pytest fixture 覆盖）
- ✅ §6 pid 存活判定 `os.kill(pid, 0) and not is_zombie(pid)` 双重校验（I4 落地）
- ✅ §3 state.json atomic write 纳入主线 I3（新增 `tests/test_waker_state_atomic.py`）
- ✅ §4 优先级链 `state.json > env > 30min default` 落 `lib/waker_state.py` schema 注释
- ✅ §7 验收模板二段式（synthetic fixture + 真实环境 smoke）落 `.cursor/skills/experiment-host/SKILL.md`
- ✅ §8 「监督者重启 server + waker 生效」列入 result_review action_items

## 立场

议题完全收敛，同意 host 推进开实验门禁（`advance-round --ready`）。T9 = `waker-status-busy-threshold-fix` 实验，I1-I8 任务划分 + ≥6 验收 case + 白名单 `^cli/`、`^lib/`、`^tests/`、`.cursor/skills/` 风险面可控。不阻塞。

旁支意见（不阻塞开实验，留待 host / executor 在 I1-I8 落地时决策）：
- I3 atomic write 与 I1 state 序列化顺序：建议 I1 先落 schema 注释，I3 再加 atomic write 路径（避免反复改 schema 字段顺序）
- §6 is_zombie 走 `/proc/<pid>/status` State 字段时，建议 mock 一个 zombie fixture（`tests/fixtures/zombie_state.json`）单测覆盖 I4，避免外部进程依赖

无 Round 3 议程。
