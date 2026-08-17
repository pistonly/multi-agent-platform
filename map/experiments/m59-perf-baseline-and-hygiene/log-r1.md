# M59 实验日志 (Round 1)

**执行时间**: 2026-08-17
**执行者**: multi-agent-platform-host
**结果**: 通过（F4 基线入库 + F5 lint 清零并锁定 + F6/F7 设计结论回写，fast-gate 全绿）

## 代码落地

### M59a scan_plane perf 基线（F4）

| 文件 | 改动 |
|------|------|
| `tests/test_fs_scan_plane_perf_baseline.py`（新增） | 50 迭代全量扫描仓库真实 `map/` 平面，p50/p95/p99 + counts 写 `.map/perf-baselines/fs-scan-plane-baseline.json`（同 phase1 机制：测试承载测量、产物稳定落盘）；slow 标记；p95 < 1.0s 防退化 tripwire（非优化触发线） |
| `sdk/python/map_fs/parser.py` | `scan_plane` docstring 补 perf note：全量扫描为有意设计；触发条件（话题 >500 或 p95 >100ms）满足时先复测基线再评估 mtime 增量 |
| `.map/perf-baselines/fs-scan-plane-baseline.json`（新增产物） | 2026-08-17 实测：7 话题 + 15 实验，p50 7.6ms / p95 10.2ms / p99 13.5ms / mean 8.1ms——距 100ms 触发线 10 倍余量 |

### M59b lint 盲区收口（F5）

| 文件 | 改动 |
|------|------|
| `alembic/**`（23 个迁移文件） | `ruff check --fix`：UP007（Union → `|`）×~110 / UP035（deprecated-import）×~36 / I001（import 排序）×~36——纯类型标注与 import 形态，revision 链与 DDL 零改动 |
| `alembic/versions/023_notification_category_aggregation.py` | SIM105 手工修复：`try/except/pass` → `with contextlib.suppress(Exception)` |
| `test_project/**`（1 文件） | I001 import 排序 |
| `.github/workflows/ci.yml`、`.github/workflows/nightly.yml` | ruff check 行扩为 `server cli sdk scripts tests alembic test_project` |
| `tests/test_lint_gate_coverage.py`（新增） | 回归锁定：断言两个 workflow 的 ruff 行覆盖 `alembic` / `test_project`（token 级匹配），防再滑出 |
| `tests/conftest.py` | `test_lint_gate_coverage` 入 `_FAST_GATE_MODULES` 白名单 |

### M59b 顺带：M58 提交引入的 gated 目录存量清理

执行中发现 `ruff check server cli sdk scripts tests` 有 16 处存量（M58 大规模测试改造提交时未过 ruff gate——本地 fast-gate 只跑 pytest 不跑 ruff，GitHub CI 在 38 commit 推送后应已红）：

| 文件 | 改动 |
|------|------|
| `server/api/topics.py`、`tests/_db_topic_factory.py`、`tests/test_cli.py`、`tests/test_mentions.py`、`tests/test_sdk.py`（多处） | I001 / F401 `--fix` |
| `tests/test_same_second_host_reply.py` | F841：删未用 `participant_headers` |
| `tests/test_todos.py` | F841 ×2：删未用 `reviewer_headers`（两处同模式，第一处编辑误删在用变量致 F821，随即恢复并改删真正未用处） |
| `tests/test_topic_progress.py` | F841：删未用 `tid` |
| `tests/test_topics.py` | F841：`comment = db_add_comment(...)` 去赋值保留调用 |
| `server/__version__.py` | fast-gate 逮住 v0.4.0 发版漏改（server.__version__ 0.3.2 vs pyproject 0.4.0，`test_eng_version_single_source` 守卫报警）——补齐 0.4.0。注：昨日发布的 PyPI 0.4.0 wheel 内嵌字符串仍是 0.3.2，属展示层瑕疵，建议随下个版本一并带出（见备注） |

### M59c PRD 回写（F6/F7）

| 文件 | 改动 |
|------|------|
| `docs/prd/v0.13.md` | 证据清单 F4/F5 行标「已落地（M59 实验）」（F5 补执行时实测 237 vs PRD 时 173 的差异说明）；F6 行「已决：不做」四条依据（slug 主标识 / DB 存量零写 / 双源 matcher 成本中等 / 重开条件）；F7 行「已决：维持 no-op + 提示」三条依据（即时投影无持久对象 / 清理语义内建 FS / DB 投影继续服务实验域）；风险表 F6 行同步已决；M59 章节执行状态块 |

## 测试与验证

- 基线测试：`pytest tests/test_fs_scan_plane_perf_baseline.py -m slow` → 1 passed（0.43s），产物落盘
- ruff 全仓：`ruff check server cli sdk scripts tests alembic test_project` → **All checks passed**（清零前：alembic 172→237 实计 + test_project 1 + gated 目录存量 16）
- 迁移链验证：`alembic current` → `046 (head)`；`alembic upgrade head` 幂等通过（UP007/UP035 改动仅类型层）
- 回归锁定：`pytest tests/test_lint_gate_coverage.py` → passed（入白名单后 fast gate 生效）
- fast-gate 全量回归：见 evidence（507+ passed / 0 failed 基线上无新增失败）

## 备注

- **发现即修的 gate 缺口**：M58 的 84 文件提交含 16 处 ruff 违规且已推 GitHub（CI 应红）——根因是本地收尾只跑 pytest fast-gate 未跑 ruff。本次顺带清零；后续实验收尾模板应含 `ruff check <gate 目录全集>` 一步（已隐含在 M59b-4 锁定测试防「目录滑出」，但不防「gated 目录内新增违规」——CI 是最后防线）。
- **F5 计数差异**：PRD 撰写时（2026-08-16）实测 173，执行时（2026-08-17）237——差额来自 M57/M58 期间新增的 045/046 迁移文件。已在 PRD F5 行留痕。
- **误删教训**：`test_todos.py` 两处相同 `reviewer_headers = reviewer["headers"]` 模式，SearchReplace 命中第一处（在用）而非 ruff 标记处——同文件多同模式编辑需先读上下文定位唯一锚点。
- **PyPI 0.4.0 内嵌版本串滞后**：发版提交只改了 pyproject / map_sdk / uv.lock，漏 `server/__version__.py`（守卫测试事后报警）。已在本实验补齐 repo 侧；已发布 wheel 内为 0.3.2（`/health` 等展示位会显示旧串），功能无影响——建议下次发版（0.4.1+）自然带出，不单独为此重发。
