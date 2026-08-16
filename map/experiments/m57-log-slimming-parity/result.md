# M57 实验结果（result）

## summary

**5/5 验收项全部达成，零遗留**。实验日志瘦身形态（`--log-file-path`）已全链路落地：schema → DB 列 → server 行为 → CLI → 测试 → 实机验证。实验计划与日志两个入口的输入形态完成对齐（M55 E8 先行 + 本实验对称补齐），长日志不再需要全量重传进 DB。

## acceptance

| # | acceptance | 结果 | 证据 |
|---|------------|------|------|
| 1 | `ExperimentLogCreate` 双形态（`content_md` / `file_path` 二选一，validator 对齐 PlanInput / ExperimentComplete）；瘦身形态 DB 存 stub（`See file: <path>`），content_md NOT NULL 兼容 | ✅ | `_require_content_or_path` model_validator；append_log stub 分支；`test_slim_form_persists_stub_path_and_increments_index` |
| 2 | `experiment_logs.file_path` 列（Text, nullable）+ 幂等 Alembic 迁移（044→045 已执行，PRAGMA 验证列存在）；`ExperimentLogRead` 透出 `file_path`，detail 读路径可见 | ✅ | `045_log_file_path.py`（inspector 幂等）；实机 GET /logs 3 条记录 file_path 全透出（见实施 log 第 3 节） |
| 3 | CLI `--log-file-path` 与 `--file` 互斥（同传 exit 2）；`--id` 复用 `_rid` 短 id 解析不回退 | ✅ | 实测：`Error: use only one of --file or --log-file-path`，exit=2，请求发出前拦截（stub transport 断言 bodies==[]） |
| 4 | 软校验处置明确且测试锁定：evidence 基于 metadata 双形态完全一致；相似度瘦身形态跳过 + `similarity_skipped: slim form` 显式标注（不误报不静默）；summary 与 prior 精确一致（==）输出非阻塞提示；`--force-skip-similarity` 瘦身形态 no-op（零审计副作用） | ✅ | `test_evidence_validation_identical_across_forms`（同 metadata 下 validation 字节一致）；实机响应 `similarity_skipped: slim form`；`test_slim_form_force_skip_is_noop_without_audit`（AuditLog `log.force_skip` 行数==0）；`test_slim_form_summary_repeat_hint`（提示含 prior summary、不阻塞、不同 summary 不触发） |
| 5 | server 回归测试（stub 落库 / log_index 递增 / 读路径透出）+ CLI 互斥测试 + fast-gate 全量回归通过 | ✅ | `tests/test_m57_log_slim_form.py` 10/10（server 7 + CLI 3）；fast-gate 全量 `483 passed, 2 skipped, 0 failed`；已登记 `_FAST_GATE_MODULES` 白名单 |

## 实施 log

### 证据链（evidence_keys 全命中，实机响应 validation.warnings == []）

1. `pytest tests/test_m57_log_slim_form.py` → **10 passed**
2. `env -u PYTHONHOME -u PYTHONPATH .venv/bin/python -m pytest tests/` → **483 passed, 2 skipped**
3. CLI 实测（8001 新代码实例重启后标准链路）：
   - 互斥同传 → exit 2（请求前拦截）
   - 本实验 log#3（503e3331）即 `--log-file-path` 瘦身形态追加：响应 `similarity_skipped: slim form`、`log.file_path` 透出、三键 evidence 全命中
   - `GET /api/v1/experiments/fe07db62.../logs` 读路径：3 条记录 file_path + stub 全透出

### 评审修订落实（plan v2）

- 73a24940：evidence 处置按 metadata 驱动事实落地（无本地检查子项），双形态一致性由测试锁定
- 9277e044：相似度改为跳过 + 标注 + summary 精确匹配提示（`summary_repeat_hint` 含两轮 summary 全文供 agent 自行判断）
- 非阻塞采纳：force_skip no-op 审计断言、ORM 字段名直配（无 alias 需求，读路径当场验证）、alembic 045 SIM102 清零

### 代码落点

- M57A：`map_types/schemas/experiment.py`（ExperimentLogCreate 双形态 + ExperimentLogRead.file_path + LogCreateResponse.similarity_skipped/summary_repeat_hint）
- M57B：`server/domain/models.py` file_path 列 + `alembic/versions/045_log_file_path.py`（已执行）
- M57C：`cli/commands/experiment.py`（--log-file-path / --file 互斥；瘦身形态不发送 force_skip_similarity）+ `cli/main.py`（summary_repeat_hint stderr HINT）
- M57D：`server/services/log_service.py`（stub 落库 / 相似度跳过 / summary 提示 / no-op）+ `similarity_service.py`（skipped_reason 字段）+ `server/api/experiments.py`（5 元组透出）
- M57E：`tests/test_m57_log_slim_form.py` + conftest 白名单

## 风险

- **低**：8001 已重启加载新代码；迁移 045 幂等（列已存在则跳过），存量 DB 无损
- **低**：实验日志流含 2 条排查期探测记录（"curl probe slim form" / "null probe"），是定位 CLI 路由问题的真实痕迹，不影响验收项
- **记录**：`map` 全局命令为沙箱独立安装（旧 schema）；实机验证须走 `.venv/bin/map`（`env -u PYTHONHOME -u PYTHONPATH` 前缀）——属环境事实，不随本实验修复

