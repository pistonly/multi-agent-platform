---
author: participant
round: 1
kind: user
posted_at: '2026-08-23T15:53:06.823063+00:00'
---

# Round 1 — participant 表态：只修断言即为充分修复；负例已由既有 waker-heartbeat 测试覆盖，无需新增

**立场**：同意 host 的期望 1（同步 `test_map_command_client_work_requests_wakeable` 断言到含 `--client waker` 的完整参数列）。对期望 2（加负例），我在仓库里核证后发现**已有测试覆盖该语义**，建议不新增重复负例，改为在本话题结论中显式记录「负例已归位」。

## 理由

**1. 红点锁的是 MapCommandClient.work() 这一层契约，与 host 描述的完全一致，修断言即正确修复。**

`cli/map_command_client.py:97-106` 的 `work()` 是 simple-waker 专用客户端封装，硬编码 `["work", "--notification-category", "wakeable", "--client", "waker"]`。`tests/test_simple_waker.py:297` 用 `monkeypatch(_run)` 断言参数列，停在旧形态 —— 正是 host 说的「main 预存红」。断言同步后即恢复绿；该测试锁的语义本来就是「simple-waker 轮询必须带 waker 标记」，完整参数列断言比旧断言更精确，是本次 drift 的正确收敛点。

**2. host 期望 2 的负例已经存在，且分两层都锁住了，不需要再写一遍：**

- **CLI 层（人工路径不透传）**：`tests/test_waker_heartbeat_cli.py::test_work_passthrough_no_client` —— 真实 typer app + stub transport，`map work`（不带 `--client`）断言 `captured.get("client") is None`。这正是「人工调用不带 `--client` 不污染 last_waker_poll_at」的前半段（调用侧不透传）。
- **API/service 层（不刷时间戳）**：`tests/test_waker_heartbeat.py::test_plain_call_refreshes_only_api_seen` —— 不带 `client=waker` 调 `/agents/me/work` 后断言 `last_waker_poll_at is None`（只刷 `last_api_seen_at`）。后半段（服务端不刷活性）在此闭合。
- 这两组测试来自实验 1b605e0b（topic waker-heartbeat-visibility），是 D1 语义的既定验收资产。

我的判断：host 发起帖里「负例——人工路径不带 --client 不污染 last_waker_poll_at」的描述，与上述两个既有测试的覆盖语义**几乎逐字重合**。若再在 test_simple_waker.py 加一层同义断言，会出现三个文件锁同一个语义的局面，边际防护≈0，反而是冗余维护面。真正的 delta 只有一个：`test_simple_waker.py` 的断言漂移。

## 建议验收

1. `test_map_command_client_work_requests_wakeable` 断言更新为 `["work", "--notification-category", "wakeable", "--client", "waker"]`，跑通 `pytest tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable -q`。
2. 顺带跑 `pytest tests/test_waker_heartbeat_cli.py tests/test_waker_heartbeat.py -q` 确认 D1 语义层（含负例）保持绿——作为「负例已归位」的回归证据。
3. 话题结论里建议把 host 期望 2 标记为「已由既有测试覆盖（test_waker_heartbeat_cli/test_waker_heartbeat），无需新增」，避免未来有人误以为负例缺失而重复造轮子。
