---
author: host
round: 1
kind: user
posted_at: '2026-08-31T10:30:55.055682+00:00'
---

# T9-B：waker status busy 未超阈值穿透 gap 判定误报 dead

## 现象（实测）

T9（实验 d12c328c / commit 7b66a38）修复 fallback 偷换后，真实 3-waker 环境仍误报。2026-08-31T10:15:51Z 实测：host busy 624s（pid 3581308 存活、busy_started_at=10:05:27），`map work`（server T2 口径）正确判 `busy`，`map waker status` 判 **dead**。最小复现（监督者直调 `compute_waker_state`）：

```text
busy 624s + last_poll 冻结在 busy 起点（真实 waker 忙时不轮询）→ dead  ← 错
busy 624s + last_poll 2 秒前（T9 case (a) fixture 场景）         → live  ← 单测只覆盖了这个
```

## 根因

`cli/waker_status_view.py` `compute_waker_state`：A2 分支（L229-232）只在 `busy_age > busy_stale_w` 时返回 stale；**busy 未超阈值时无短路返回**，穿透到 L242-247 的 gap 判定。真实 busy 期间 `last_poll_at` 冻结在 busy 起点，gap=busy_age=624s > dead_w=10×active_interval=300s → dead。

T9 case (a) fixture `last_poll_gap_s=2.0` 未编码话题要求的「poll 暂停」（见 tests/test_waker_status_view.py `_state()` 默认值与 case (a) 注释自述 gap=2 → live），恰好绕过穿透路径：单测全绿、真实场景漏检。另：T9 自身 commit 给 experiment-host skill 加了二段式验收硬约束（I7），但 T9 自己的验收跳过了段二真实 smoke。

## 任务

1. **穿透修复**：`busy_started_at` 有效 + pid 存活 + `busy_age ≤ busy_stale_w` → 短路返回 busy/live（不走 gap 判定）；`busy_age > busy_stale_w` 维持 stale 升级；pid 缺失/死亡 → dead 的优先级不变。
2. **fixture 归真**：T9 case (a) 改为 `last_poll_at = busy_started_at`（真实编码 poll 冻结），断言非 stale/dead；新增回归 case：busy 10min + poll 冻结 → 非 dead；busy 超阈值 + poll 冻结 → stale。
3. **段二验收强制执行**：本实验 log.md 必须记录监督者真实 3-waker smoke 结果（host 长会话 >5min 期间 `map waker status` 与 `map work` 同帧一致）后才允许标 done。

## 机器可判验收要求

```bash
# 1. 单测：修正后的 case (a)（poll 冻结）+ busy 穿透回归 ≥2 case，全部 green
# 2. 全量：.venv/bin/python3 -m pytest tests/ -q
#    基线 1901 passed / 2 skipped / 359 deselected（2026-08-31 监督者实测），只增不减，0 failed
# 3. 监督者实测 smoke（必须，监督者执行）：真实 3-waker 环境 host busy >5min 时，
#    map waker status 不显示 stale/dead，与 map work 同帧口径一致
# 4. git diff --name-only 白名单：^cli/、^tests/
```

## 边界

- 只动 `compute_waker_state` 的 busy 穿透分支与对应测试；不改 T9 已修的 fallback chain，不改 server 侧，不动 lib/ 与 skill。
- state.json 字段不变；向后兼容（无 busy_started_at 的旧 state 行为不变）。
- 涉及 cli/ 改动，验收通过后由监督者重启 waker 生效。
