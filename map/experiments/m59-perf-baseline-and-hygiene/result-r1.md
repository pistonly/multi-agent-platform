# M59 结果提交（性能基线与工程卫生）

## summary

v0.13 M59 三交付面全部落地：F4 `scan_plane` perf 基线入库（7 话题 + 15 实验，p95 10.2ms，距 100ms 触发线 10 倍余量，测量测试可重复）+ 触发条件注释落 `parser.py`；F5 lint 盲区收口——alembic + test_project 237 errors 清零、CI 两 workflow gate 行纳入两目录、回归锁定测试入 fast gate 白名单，顺带清掉 M58 提交引入的 16 处 gated 目录存量与 v0.4.0 发版漏改的 `server.__version__`；F6/F7 设计结论已决回写 PRD（F6 不做 / F7 维持 no-op + 提示，依据链完整留痕）。

## 实施 log

- R1 完整日志与证据链：[log-r1.md](log-r1.md)（代码落地明细、基线实测数据、lint 计数差异说明、误删教训）
- 基线产物：[fs-scan-plane-baseline.json](../../../tests/perf-baselines/fs-scan-plane-baseline.json)（counts 快照随文件走，复测可区分规模驱动与代码驱动漂移）
- 测量命令：`pytest tests/test_fs_scan_plane_perf_baseline.py -m slow`
- 回归锁定：[test_lint_gate_coverage.py](../../tests/test_lint_gate_coverage.py)（token 级断言 CI ruff 行覆盖两目录）
- PRD 回写：[v0.13.md](../../docs/prd/v0.13.md) F4/F5 已落地标注、F6/F7 已决标注（证据清单 + 风险表）、M59 执行状态块

## 风险

- **PyPI 0.4.0 内嵌版本串滞后**：已发布 wheel 内 `server.__version__` 为 0.3.2（发版提交漏改，守卫测试事后报警）；repo 侧已补齐，展示层瑕疵功能无影响，建议随下个发版自然带出，不单独重发。
- **本地收尾不含 ruff**：M58 的 16 处存量暴露「fast-gate 只跑 pytest」缺口——M59b-4 锁定测试防目录滑出，但 gated 目录内新增违规仍靠 GitHub CI 兜底（本次已清零，推送后应转绿）。
- **基线机器噪声**：与 phase1/phase2 同口径（分位数 + note 标注）；断言阈值 1.0s 为防退化 tripwire，非优化触发线（100ms 在注释中）。

## acceptance

- ✅ F4：基线文件入库 + 可重复测量命令 + `scan_plane` docstring 触发条件注释（话题 >500 或 p95 >100ms 再评估 mtime 增量）
- ✅ F5：`ruff check server cli sdk scripts tests alembic test_project` All checks passed；CI 两 workflow ruff 行含两目录；`test_lint_gate_coverage` 入 fast gate 白名单且通过；`alembic upgrade head` 幂等验证
- ✅ F6：已决「不做」回写 PRD（slug 主标识 / DB 存量零写 / 双源 matcher 成本中等 / 重开条件四条依据）
- ✅ F7：已决「维持 no-op + 提示」回写 PRD（即时投影无持久对象 / 清理语义内建 FS / DB 投影继续服务实验域三条依据）
- ✅ fast-gate 508 passed / 0 failed；PRD v0.13 M59 章节执行状态块已补
