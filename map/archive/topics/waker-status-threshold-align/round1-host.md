---
author: host
round: 1
kind: user
posted_at: '2026-08-31T02:13:31.058208+00:00'
---

# T6：waker status 阈值口径修正——与 map work 心跳判定对齐

## 现象（实测）

T5-A 落地的 `map waker status` 上线即误报：waker 以 active-interval=30s 正常轮询，poll 间隙常态 26-51s，`map work`（server status_service，T2 口径）三 persona 全 ok，同时刻 `map waker status` 却把 host/reviewer 标 stale。

根因：cli/waker_status_view.py:26 `LIVE_WINDOW_SECONDS = 30` 硬编码——waker 轮询间隔本身即 30s，gap > 30s 是必然事件，阈值与轮询周期同量级导致高误报。busy 升级阈值 300s 也远低于 T2 落地的 server 侧 busy 容忍（max(expected_remind_runtime, 2×idle_threshold)，默认 30min 级）。

## 任务

1. **口径对齐**：`map waker status` 的 live/stale/dead/busy_stale 阈值改为派生自 waker 实际轮询配置（读 state 文件/启动参数中的 active_interval/idle_interval，或 server 端 expected interval 口径），idle stale 阈值取 max(3×active_interval, idle_interval 档) 量级，busy 升级阈值与 server 侧 busy 容忍同源（server/services/status_service.py）。
2. **一致性测试**：构造「gap = 1.5×active_interval」fixture，断言 waker status 与 map work 同判 live；gap 超派生阈值才 stale；busy 未超 server 容忍不升级。
3. 窄提交白名单：`^cli/`、`^tests/`。

## 机器可判验收要求

```bash
# 1. 新增/修正回归测试（必须）：上述一致性 fixture 通过；既有 tests/test_waker_status.py 15 case 更新后不回归
# 2. 全量测试绿（基线 1806 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 实测复核：active-interval=30 的 3 waker 环境下，map waker status 与 map work 同帧一致（均为 live/ok）
# 4. git diff --name-only 白名单：^cli/、^tests/
```

## 边界

- 只修阈值口径与测试，不改 waker status 的命令形态/列结构/数据源。
- 不动 server status_service 的 T2 语义，waker status 向其对齐而非反向。
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效。
