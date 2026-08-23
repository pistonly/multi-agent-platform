---
title: "fast-gate 白名单反转：默认跑 + 显式 marker 排除，删除 _FAST_GATE_MODULES 漂移源"
acceptance:
  - "A1 基线测量（D3 前置）：反转前测量全量非 integration/claude_cli 套件运行时长，输出报告（含 per-module 耗时 top-N，`--durations` 一行参数，同时喂 A4 打标候选定位）"
  - "A2 假绿消除：无 marker、无登记的新测试文件默认被 `pytest` 收集并运行（新增一个探针测试文件验证：写一个必败用例，确认它真的跑了且 CI 红）"
  - "A3 删除完成：`_FAST_GATE_MODULES` frozenset（tests/conftest.py）与 `test_eng_fast_gate_whitelist_complete` 自身一致性守卫删除后，fast-gate 路径全绿"
  - "A4 存量分类：原 deselected 集合中真 slow/集成用例显式打标（`@pytest.mark.slow` / `integration` / `claude_cli`），反转后全量 deselect 数与打标数一致（无静默残留；候选定位用 A1 的 per-module top-N）"
  - "A5 时长达标：反转后默认套件运行时长在快反馈阈值内（对照 A1 基线裁决阈值）；若超阈值，落地兜底入口（保留 `pytest -m \"not integration and not claude_cli\"` 为常用入口）+ 文档说明"
  - "测试面：fast-gate 路径自身回归全绿；conftest 变更后的收集行为有单测覆盖（marker 显式排除生效、无 marker 默认收集）"
evidence_keys:
  - "pytest 快速门控全量回归通过（反转后语义）"
  - "实测：探针测试文件（无 marker 无登记）被默认收集运行（A2）；`pytest --collect-only -q` deselect 数与显式打标数一致（A4）"
  - "测量报告：反转前全量套件时长 + per-module top-N（A1）；反转后默认套件时长（A5）"
  - "grep 核证：conftest 无 `_FAST_GATE_MODULES`；`test_eng_fast_gate_whitelist_complete` 已删；兜底入口文档（如触发 A5 后半分支）落盘"
dependencies:
  - "话题 fast-gate-allowlist-inversion（d8986d19-7768-52e3-9432-9c3415a861ed）Round 2 定稿决议 D1–D3：participant 两个修正（删 frozenset+守卫、时长基线前置）均吸收为验收前提"
  - "同批体验话题 host-invoke-async 的 invoke 改造不依赖本实验；本实验只动 tests/conftest.py + pyproject.toml + 存量测试文件 marker"
---

# fast-gate 白名单反转：默认跑 + 显式 marker 排除，删除 _FAST_GATE_MODULES 漂移源

## 背景

话题 `fast-gate-allowlist-inversion`（发起帖 2026-08-23 实测）：`tests/conftest.py` 的 `_FAST_GATE_MODULES` frozenset 是 opt-in 机制——不在白名单的测试文件被 `add_marker(slow)` + pyproject `addopts -m 'not slow and not integration and not claude_cli'` 静默 deselect（conftest.py:270 + pyproject.toml:107），CI 不跑、无任何警告。后果是「不登记 = 假绿」：测试写了、本地全量过、CI 绿，但实际没跑。

实测受害者（发起帖）：`test_project_config`（含刚修的 `--persona` 长名兼容用例，修复者的测试根本没进 CI）、`test_feedback.py` / `test_feedback_admin_cli.py`（CI 从没跑过，直到 v0.15 M62 删除时才发现）；v0.14/v0.15 两批实验都把「新测试必须显式入白名单」写成硬性验收条目——把补偿流程写进验收本身就是症状。佐证继续累积：waker-heartbeat 实验的 `test_waker_heartbeat` / `test_waker_heartbeat_cli` 又是手工登记进白名单守契约的。

Round 2 participant 表态同意反转且零保留，贡献两个关键修正（均吸收为决议）。

## 定稿决议（来自话题 Round 2 Summary，D1–D3）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 反转默认判据：deselect 依据从「不在 `_FAST_GATE_MODULES`」改为「文件内显式 marker（`slow` / `integration` / `claude_cli`）」——marker 语义已存在于 pyproject addopts，反转后单一事实源 | 双方共识 |
| D2 | **直接删除** `_FAST_GATE_MODULES` frozenset 与 `test_eng_fast_gate_whitelist_complete` 自身一致性守卫——分类只信文件内显式 marker，不再维护第二份「模块名 ↔ 集合」映射（漂移源本身消失，守卫随之失业）；存量 1092 deselected 用例无需逐个分类，只需给真 slow/集成/真 CLI 打标 | participant 洞见 1（比发起帖选项 1 更进一步） |
| D3 | 反转**前置**全量非 integration/claude_cli 套件时长基线测量，作为实验验收条目而非可选项——防反向灾难（默认从假绿变成过慢）；若基线超快反馈阈值，保留 `pytest -m "not integration and not claude_cli"` 常用入口并文档化 | participant 洞见 2 |

## 调研事实（Round 2 已核证）

| 事实 | 位置 |
|------|------|
| deselect 机制：白名单外模块 `add_marker("slow")`，pyproject `addopts = "-m 'not slow and not integration and not claude_cli'"` 将其过滤 | `tests/conftest.py:260-280`、`pyproject.toml:104-107` |
| 白名单自身一致性守卫存在且依赖 frozenset | `test_eng_fast_gate_whitelist_complete`（conftest 白名单注释引用） |
| marker 语义（slow/integration/claude_cli）已在 addopts 与既有测试文件中广泛使用 | `pyproject.toml:107`、存量测试文件 |
| 存量 deselected 规模 ~1092 用例（发起帖实测），绝大多数是「没人登记」而非「真慢」 | 话题发起帖 |

## 实施顺序（建议，评审可调）

1. **I1 基线测量**（A1）：全量非 integration/claude_cli 套件跑一次，记录时长 + `--durations` per-module top-N → 裁决 A5 阈值口径
2. **I2 反转 conftest**（D1+D2+A3）：删 `_FAST_GATE_MODULES` 与守卫测试；`pytest_collection_modifyitems` 只保留「显式 marker 才排除」的既有分支
3. **I3 存量打标**（A4）：按 I1 top-N 定位真慢/集成/真 CLI 用例，补显式 marker；核对 deselect 数一致
4. **I4 探针与时长复验**（A2+A5）：探针测试文件验证默认收集；反转后默认套件时长复测对照阈值
5. **I5 文档**：兜底入口（若触发）+ 实验日志记录全部测量数据

## 风险与边界

- 反转后默认套件时长超阈值的风险由 D3 前置测量兜住（A1→A5 闭环）
- 删除守卫测试后「白名单一致性」语义消失——这正是 D2 的目的（单一事实源是文件内 marker，无需守卫）
- `claude_cli` marker 的既有排除语义不变（真调 Claude CLI 的测试仍默认排除，防 CI 非确定性）
