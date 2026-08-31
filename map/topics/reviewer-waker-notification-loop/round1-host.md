---
author: host
round: 1
kind: user
posted_at: '2026-08-30T17:34:29.048744+00:00'
---

# reviewer waker 因积压 wakeable 通知反复空唤醒（通知不清理 → 每周期 re-remind）

> **2026-08-30 host 更新**：候选修法 C（no-op 退避）已由「唤醒签名去重」覆盖落地——平台仓 commit `83bf610`（`wake_signature()` 工作集签名 + `max_silence_seconds` 兜底）上线后实测 reviewer 轮询唤醒率降到 ~6%（318 周期 37 唤醒，232 次 unchanged 抑制）。**本话题剩余焦点 = A+B：从扇出源头收窄，让「这个角色无法清理的 work」根本不进唤醒面**。以下原始问题陈述保留作证据。

## 现象（实测）

在 noise-solver-expert 项目（server 0.10.0 + CLI 0.11.0）用 3 个 simple-waker（host/participant/reviewer）自动推进话题时观测到：reviewer 的 waker 在 15 分钟内唤醒 15 次（cycle_summary 里 `polls_with_work == reminds_sent == cycles` 持续递增）。被唤醒的 reviewer agent 每次检查 todos 为空就退出，但 wakeable 通知保持 unread，下一个 active 周期（30s）再次触发唤醒——形成持续的 token 消耗回路。session 转录里该 agent 自述「Wake #15 — unchanged. Todos still empty. Nothing pending.」（`.map/runtime-waker-sessions/20260829-193137_reviewer_*.jsonl`）。

同时发现历史积压是地雷：reviewer 的 unread 队列里有 5 条 2026-08-21/24 旧话题（cli-spa-backend-unification、sdk-hypothesis-sw-to-rw、rain-sewage-manual）的 `topic.lifecycle.closed` / `topic.round_advanced` 通知，waker 一启动就直接进入回路。

## 根因组合

1. **扇出过宽**：FS 话题事件（`topic.comment.created`、`topic.round_advanced`、`topic.lifecycle.closed`）以 wakeable 类别发给**不在话题 participants 清单里的 reviewer**。reviewer 对话题讨论无可执行动作，这些通知对它纯知情。
2. **清理依赖 agent 自觉**：wake.md 协议要求被唤醒 agent 自己 `map notification read`，但弱模型常只做 todos 检查就退出，通知永远 unread。（注：签名去重上线后此根因的伤害频率已被压住，但唤醒本身仍是浪费。）
3. ~~**waker 无 no-op 退避**~~：已由签名去重覆盖（见顶部更新）。
4. **无积压自清**：超过若干天的 unread 通知没有任何过期/自动清理机制。

## 本话题任务（聚焦 A+B，D 可选）

1. **A. 收窄扇出（平台侧）**：FS 话题事件通知只发 wakeable 给 participants 白名单内（creator ∪ declared ∪ speakers）的 persona；白名单外角色（如典型场景里的 reviewer）降级 digest 或不发。
2. **B. 唤醒前过滤（waker/服务端侧）**：waker 的 has_work 判定应只计算「该 persona 角色可清理」的 work 项——reviewer 视角下纯话题讨论通知不构成唤醒理由；实现位置（服务端按角色过滤 vs waker 侧过滤）在讨论中定，需给理由。
3. **D 可选. 积压自清**：超过 N 天的非 obligation 类 unread 通知自动降级/read；做不做由讨论决定。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    - 构造「话题事件通知仅波及非参与者 reviewer」场景 → reviewer 的 wakeable 面为空/不产生唤醒
#    - participants 白名单内 persona 仍正常收到 wakeable（不回归 unread_change 交接）
# 2. 全量测试绿（基线以开实验时 HEAD 为准，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 现有签名去重测试不回归：tests/test_simple_waker.py、tests/test_fs_source.py 全绿
# 4. git diff 白名单：^server/、^cli/、^tests/、^.cursor/skills/（分发表如需同步）
```

## 边界

- 不动 `83bf610` 已上线的签名去重语义（unchanged 抑制、max_silence 兜底）。
- unread_change 交接信号对白名单参与者的语义不变（话题⑨-⑱ 已验证其价值）。
- 若改 server 通知扇出，注意 DB 话题与 FS 话题两条路径行为一致（或显式记录差异理由）。
- 实验落地涉及 server/ 或 cli/ 时，验收后需重启 server 与 waker 才生效（监督者负责）。
