# A1 反转前基线测量报告（2026-08-23）

> 命令：`python -m pytest -m "not integration and not claude_cli" -q --durations=50`
> 环境：dogfood 本机（docker server 运行中，共享 DB）

## 核心数据

| 指标 | 值 |
|------|-----|
| 全量收集（非 integration/claude_cli） | **1689 / 1703**（14 显式 deselected） |
| 默认白名单内收集 | 603 / 1703（**1100 deselected**：白名单机制 1086 + 显式 14） |
| **全量总时长** | **1612.80s（26:52）** |
| 结果 | **52 failed**, 1631 passed, 6 xfailed |
| 历史 fast gate 阈值 | 90s（`scripts/test-fast.sh` GATE_MAX_SECONDS） |
| 历史参考（PERF.md 2026-07-06） | 597 例 1009s（`-m "not slow"` 误用形态） |

## A5 阈值口径裁决依据

- 全量形态 1612s ≫ 90s：**不可能**让反转后默认套件直接当 fast gate
- 打标真 slow 后的默认套件时长待 I3 完成复测（A5 正式验收）；预期主体为快速单测（本次 durations 尾部显示大量 3-4s 的 setup，慢集中在 waker_phase2 系列，见下）
- 兜底入口预案（A5 后半）：保留 `pytest -m "not integration and not claude_cli"` / 调整 test-fast.sh 语义，随 I3/I4 结果定稿

## durations 局部记录

> 完整 durations=50 输出被截断（tail -80 只留尾部），最慢段丢失；per-module 聚合以 PERF.md 历史 + 本节局部 + I3 打标后 `--collect-only` 对账补偿。

- 尾部可见最慢 ~3.5-3.8s，多为 **setup**（TestClient/DB fixture 构建）：test_accept_result_verdict / test_template_api_contract / test_similarity_api_contract / test_experiment_executor / test_m3_flow 系列
- PERF.md 历史慢用例（本次在 1689 内、I3 头号 slow 候选）：test_waker_phase2_e2e_a1_total（126s/31s）、test_waker_phase2_acceptance（30s/14s/5.8s）、test_waker_phase2_e2e_a1a（16s）

## 52 个失败的 triage（摘要）

**这是「假绿」命题的核心实证：52 个失败用例全部位于白名单机制静默 deselect 的 1086 例中——3 个月无人发现。**

| 类别 | 代表 | 初判 |
|------|------|------|
| 环境状态污染 | `test_cli.py` 系列（`--q` 过滤返回 136 条 dogfood 历史实验致断言失败） | 环境性（共享 DB 状态依赖） |
| 库版本 | `test_mcp.py` 4 例（`mcp.server.fastmcp` 异常） | 环境性 |
| import 链 | `test_map_sdk_skeleton.py`（map_sdk 导入 server 守卫） | 待查（真坏候选） |
| API 演进未跟 | `test_m2_flow.py::test_full_review_flow`（assert 422 == 200；PERF.md 7 月即 409==422，语义又漂移） | 真坏候选 |
| 其余 assert 类 | test_cli_error_envelope / test_todos / test_error_codes_cli 等 | 待分类 |

**处置边界（与 reviewer 认可的变更面一致）**：本实验只做机制反转（conftest + marker + 文档），不修 52 个既有失败——它们是反转**暴露**的债务而非反转引入的回归。反转后默认套件出现红 = 真实状态。修复建议：收敛后开后续话题分环境性修复（fixture 隔离）与真坏修复（跟 API 演进）两批处理。

## A4 对账基线

- 反转前 deselect：1100（= 白名单机制 1086 + 显式 integration/claude_cli 14）
- 反转后预期 deselect：显式 slow（I3 新增打标）+ 14——两者差值应等于「由白名单机制 deselect 且未打标」的用例归零
