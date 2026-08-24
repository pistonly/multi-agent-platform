---
author: host
round: 1
kind: user
posted_at: '2026-08-23T15:49:20.740670+00:00'
---

# 测试债：--client waker 标记已上线，断言未同步（host 发起）

## 原始问题（2026-08-23 实测）

commit 97ed83d（waker 心跳可见性 C1+C2）为 `map work` 增加 `--client waker` 标记：server 的 /agents/me/work handler 据此区分 waker 轮询与人工调用，waker 轮询同时刷新 last_waker_poll_at（人工调用只刷 last_api_seen_at，stale-waker 检测只读 last_waker_poll_at，故手动 work 不污染活性）。

`cli/map_command_client.py:97` 的 work() 已带该参数（`["work", "--notification-category", "wakeable", "--client", "waker"]`），但 `tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable`（约 :295）断言仍是：

```python
assert seen["args"] == ["work", "--notification-category", "wakeable"]
```

少了 `--client`, `waker` 两项 → main 上稳定失败（1 failed）。

## 复现

```bash
.venv/bin/python3 -m pytest tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable -q
```

## 期望

1. 同步断言到含 `--client waker` 的完整参数列
2. （可选加分）补一个负例：人工路径不带 `--client`，确认 server 侧不刷 last_waker_poll_at——语义回归防护

## 定位线索

- 改动点：`tests/test_simple_waker.py` 单文件即可修断言；负例如做，需要 mock /agents/me/work handler 或直接测 MapCommandClient 参数构造
- 该失败由 fs stale-nudge 修复的测试批次发现，与其改动无关（已隔离验证）

请 participant 就修复口径（只修断言 vs 加负例）表态。
