# pytest fast gate — duration baseline

> 生成命令：`python3 -m pytest --durations=0 -m "not slow"`
> 生成时间：2026-07-06（实验 fc9235fb PR2）

## 汇总（fast gate：`not slow and not integration and not claude_cli`）

| 指标 | PR2 误用 `-m "not slow"` | PR3 修正后 |
|------|--------------------------|------------|
| 收集用例 | 597 | **112** |
| 总耗时 | 1009s | **~6s** |
| gate | 无 | **<90s 通过** |

> PR2 baseline 曾用 `-m "not slow"` 误含 integration 套件；PR3 以 `test-fast.sh` 与 conftest 默认 slow 分层为准。

## 历史 baseline（PR2，仅供参考）

| 指标 | 值 |
|------|-----|
| 通过 | 597 |
| 失败 | 3 |
| 总耗时 | 1009.37s |

## 失败用例（baseline 当次）

1. `tests/test_m2_flow.py::test_full_review_flow` — assert 409 == 422
2. `tests/test_waker_phase2_e2e_a1a.py::test_sse_transmission_delay_under_1s`
3. `tests/test_waker_phase2_e2e_a1a.py::test_sse_subscriber_misses_no_notification_in_burst`

## 最慢 call（>2s，节选）

| 耗时 | 用例 |
|------|------|
| 126.27s | `test_waker_phase2_e2e_a1_total.py::test_a1_total_p95_by_kind_under_redline` |
| 31.56s | `test_waker_phase2_e2e_a1_total.py::test_a1_total_baseline_emitted` |
| 30.21s | `test_waker_phase2_acceptance.py::test_a5_replay_rejection_holds_across_sources` |
| 16.72s | `test_waker_phase2_e2e_a1a.py::test_sse_subscriber_misses_no_notification_in_burst` |
| 14.71s | `test_waker_phase2_acceptance.py::test_a4_replay_replay_path_only_one_row` |
| 5.97s | `test_waker_phase2_e2e_a1b.py::test_real_claude_cli_subprocess_startup` |
| 5.83s | `test_waker_phase2_acceptance.py::test_a4_sse_path_replay_rejected` |
| 5.05s | `test_waker_phase2_e2e_a1a.py::test_sse_a1a_p95_baseline_emitted` |
| 4.04s | `test_sdk.py::test_sdk_notifications` |
| 3.89s | `test_todos.py::test_pending_topic_replies` |

完整 duration 列表见当次 CI/本地 `pytest --durations=0 -m "not slow"` 输出。

## 复现

```bash
./scripts/test-fast.sh --durations=0 -q
```
