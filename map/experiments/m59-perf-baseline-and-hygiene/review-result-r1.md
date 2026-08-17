# M59 结果核验（reviewer）

## 核验结论

接受。F4/F5/F6/F7 四项交付面全部达成，验收证据链完整。

## 核验要点

1. **F4 基线**：产物 `.map/perf-baselines/fs-scan-plane-baseline.json` 入库（7 话题 + 15 实验，p50 7.6ms / p95 10.2ms / p99 13.5ms）；测量测试 slow 标记、命令可重复；`scan_plane` docstring 含触发条件（话题 >500 或 p95 >100ms）与「先复测再优化」流程——与 PRD「观测先于优化」一致。
2. **F5 lint**：全仓八目录 `ruff check` All checks passed（执行计数 237 与 PRD 173 的差异已留痕解释——045/046 迁移为 PRD 撰写后新增）；CI 两 workflow ruff 行纳入两目录；`test_lint_gate_coverage` 回归锁定入 fast gate 白名单；`alembic current` 046 head + upgrade 幂等——迁移文件批量类型化改动的风险有实证兜底。
3. **F6/F7 已决回写**：PRD 证据清单与风险表两处标注；F6「不做」四条依据（slug 主标识 / DB 存量零写 / 双源 matcher 成本 / 重开条件）与 F7「维持 no-op + 提示」三条依据（即时投影 / 清理语义内建 / DB 投影服务实验域）均代码级可复核。
4. **执行中发现即修**：M58 提交引入的 16 处 gated 存量清零（暴露本地收尾不含 ruff 的缺口，GitHub CI 为兜底）；`server/__version__.py` 0.4.0 补齐（守卫测试报警处置）；PyPI wheel 内嵌旧串的处置建议（随下版带出）记录在案。
5. **回归**：fast-gate 508 passed / 0 failed（新增锁定测试计入）。

## 遗留（不阻塞）

- PyPI 0.4.0 wheel 内嵌版本串 0.3.2——展示层瑕疵，随下个发版带出。
- 本地实验收尾模板建议补 `ruff check`（gate 目录全集）一步，防再依赖 CI 兜底。
