# pytest fast gate — duration baseline

> 生成命令：`TMPDIR=$(mktemp -d) python3 -m pytest -n auto -q --durations=25`
> 生成时间：2026-09-14（T46，替换 2026-07-06 的旧 baseline）
> 环境：macOS / Python 3.11.8 / `-n auto`（8 并发）；CI（ubuntu-latest）单 leg 约 380s，约为本机 1.5 倍。

## 汇总（fast gate：`not slow and not integration and not claude_cli`）

| 指标 | 2026-07-06 旧 baseline | 2026-09-14 实测 |
|------|------------------------|-----------------|
| 收集用例 | 112 | **2625** |
| 通过 | — | 2615 |
| 失败 | — | 1（本机沙箱假失败，见下） |
| 墙钟（`-n auto`） | ~6s | **~245s** |
| gate 预算 | 90s（恒红失效） | **300s** |

> 用例两年内增长 23 倍，主要来自 FS 事实源、迁移 manifest、work kind 与本次新增的
> 「模块行数全仓扫描」（+280 例）。90s 预算自 2026-08 起就是必然失败的死门禁，
> T46 按实测重设为 300s（留约 20% 余量）。历史细节见 `docs/archive/`（如有）。

## 已知失败（本机，非 CI）

- `tests/test_waker_status_view.py::test_case_f_atomic_write_race_reader_retries_success`
  —— **WorkBuddy 沙箱假失败**：shim 的 broker 自身也调用 `json.loads`，导致
  「全局 patch `json.loads` + 断言精确调用次数」多计一次。真实 CI 通过。

## 最慢 25 项（call，节选）

| 耗时 | 用例 |
|------|------|
| 30.5s | `test_eng_mypy_strict_project_service.py::test_project_service_py_passes_mypy_strict` |
| 28.6s | `test_eng_mypy_strict_project_service.py::test_baseline_mypy_clean_for_project_service_py` |
| 13.2s | `test_dual_root_isolation.py::test_dual_root_topic_list_reads_project_root_only` |
| 12.7s | `test_dual_root_isolation.py::test_dual_root_topic_write_only_touches_project_root` |
| 11.9s | `test_eng_mypy_strict_todo_service.py::test_todo_service_passes_mypy_strict` |
| 11.0s | `test_eng_mypy_strict_experiments_projects.py::test_experiments_py_passes_mypy_strict` |
| 10.9s | `test_eng_mypy_strict_topic_work_item_service.py::test_topic_work_item_service_passes_mypy_strict` |
| 9.2s | `test_dual_root_isolation.py::test_explicit_project_root_beats_upward_search` |

> 完整 25 项见 `--durations=25` 输出；榜单里 20/25 是 `test_eng_mypy_strict_*`。

## 最大的加速杠杆（未做，供排期）

1. **`tests/test_eng_mypy_strict_*.py` 占最慢榜单 20/25** —— 每个用例都起一次 mypy
   子进程（冷启动 8-30s），合计约 4 分钟。它们是「门禁有效性」的验证而非业务回归，
   建议整体打 `slow` marker 移出 fast gate（nightly 仍跑），可把墙钟砍掉一半以上。
2. `tests/test_dual_root_isolation.py` 三个用例各 9-13s（真起 workspace / 多进程），
   同样适合降级 `slow`。
3. 覆盖率：`--cov` 再加约 20-30s（子进程数据合并）。PR gate 保留即可，
   **必须与 `--cov-branch` 成对出现**（否则 `combine()` 抛 `DataError`，见 ci.yml 注释）。
