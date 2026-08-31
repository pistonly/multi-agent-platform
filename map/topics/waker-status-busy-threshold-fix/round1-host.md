---
author: host
round: 1
kind: user
posted_at: '2026-08-31T09:24:36.066080+00:00'
---

# T9：waker status busy 档口径修正——expected_remind_runtime 真实同源

## 现象（实测）

T6（a8b64c20）修复 idle 档误报后，busy 档仍误报：waker 进长会话（busy 超 3 分钟）时 `map waker status` 升级 stale、超 5 分钟标 dead；同时刻 `map work`（server T2 口径）正确显示 busy。监督者巡检期间 host 长会话常态触发，CLI 视图在最关键的「waker 正忙」场景不可用。

## 根因

`cli/waker_status_view.py:154`：busy 升级阈值 `busy_stale(expected_remind_runtime=idle_stale_w, idle_threshold=idle_stale_w)` = 2×idle_stale = 180s——expected_remind_runtime 缺省 fallback 偷换语义。server 侧 T2 口径（server/services/status_service.py）是 `max(expected_remind_runtime_minutes=30min, 2×idle_threshold)`。T6 任务书「busy 升级阈值与 server 侧 busy 容忍同源」未落实（T6 验收只测了 idle 档同帧一致性，busy 档漏核——监督者验收盲区，本话题同时补验收模板）。

## 任务

1. **真实同源**：CLI 侧 busy 容忍取 waker 实际 expected_remind_runtime——waker state.json 序列化 `expected_remind_runtime_seconds`（T2 已有 config 项 expected_remind_runtime_minutes + env MAP_EXPECTED_REMIND_RUNTIME_MINUTES），`map waker status` 读取该字段；缺失时 fallback 到 env/默认 30min（不是 idle_stale）。
2. **同帧一致性测试**：构造 busy 5 分钟 fixture（state.json busy_started_at 5 分钟前 + pid 存活 + poll 暂停）→ `map waker status` 判 busy（非 stale/dead），与 server `map work` 口径一致；busy 超 expected_remind_runtime 才升级。
3. **验收模板补硬约束**：视图类实验验收必含「新 CLI 命令实跑 smoke」（T6 打包遗漏 + 本 busy 档漏核的教训），写入 experiment-host skill 或话题模板。
4. 窄提交白名单：`^cli/`、`^lib/`、`^tests/`、`.cursor/skills/`（验收模板条款）。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：busy 5min fixture 同帧一致 + 超阈值升级 + 缺字段 fallback 30min
# 2. 全量测试绿（基线 1875 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 实测 smoke（必须）：真实 3-waker 环境下 host 进长会话，map waker status 显示 busy 且与 map work 一致
# 4. git diff --name-only 白名单：^cli/、^lib/、^tests/、^.cursor/skills/
```

## 边界

- 不改 server T2 的 busy 容忍公式本身；CLI 向其同源。
- 不改 live/idle_stale/dead 档（T6 已对齐，实测无误报）。
- state.json 新增字段向后兼容（缺字段 fallback，不破坏旧 state）。
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效。
