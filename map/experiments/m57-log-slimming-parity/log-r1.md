# M57 实施记录 r1（瘦身形态首条日志）

> 本文件即 evidence 3 的实测对象：通过 `map experiment log --log-file-path` 以瘦身形态追加，服务器侧仅存 stub 与本路径。

## 1. 代码落地（M57A–D）

| 子项 | 落点 | 内容 |
|------|------|------|
| M57A schema | `sdk/python/map_types/schemas/experiment.py` | `ExperimentLogCreate` 双形态（`content_md` 可选 + `file_path` 新增 + `_require_content_or_path` validator，对齐 `PlanInput` / `ExperimentComplete` 先例）；`ExperimentLogRead` 透出 `file_path` |
| M57B DB | `server/domain/models.py` + `alembic/versions/045_log_file_path.py` | `experiment_logs.file_path`（Text, nullable）行级列；迁移幂等（inspector 检查，044→045 已执行，PRAGMA 验证） |
| M57C CLI | `cli/commands/experiment.py` | `--log-file-path`（str 原样透传，本地不读文件）；与 `--file` 互斥同传 exit 2；瘦身形态不发送 `force_skip_similarity`（协议级 no-op） |
| M57D server | `server/services/log_service.py` + `similarity_service.py` + `api/experiments.py` | stub 落库（`See file: <path>`）；相似度跳过 + `SimilarityValidationResult.skipped_reason`（响应 `similarity_skipped: slim form`）；summary 与 prior 精确一致（==）触发非阻塞 `summary_repeat_hint`；`force_skip_similarity` 瘦身形态零副作用（无 audit 行） |

## 2. 测试（M57E）

- 新增 `tests/test_m57_log_slim_form.py`：10 例全绿（server 7 + CLI 3）
  - server：stub 落库 / file_path 读路径透出 / 相似度跳过标注 / force_skip no-op（断言 `log.force_skip` audit 行数为 0）/ summary 精确一致提示（不同 summary 不触发）/ evidence 双形态一致性（同 metadata 下 `validation` 字节一致）/ 二选一 422 / 全量形态回归（相同 body 两次仍触发 `HIGH_CONTENT_SIMILARITY`）
  - CLI：同传互斥 exit 2（发请求前拦截）/ `--file` 回归（payload 携带 content_md）/ 瘦身形态 payload 携带 file_path 且不带 force_skip_similarity
- 已登记 fast-gate 白名单（`tests/conftest.py` `_FAST_GATE_MODULES`）

## 3. 证据（对齐 plan evidence_keys）

1. **pytest 新增 m57 瘦身测试全绿**：`10 passed`（`tests/test_m57_log_slim_form.py`）
2. **pytest 快速门控全量回归通过**：`483 passed, 2 skipped, 0 failed`（`env -u PYTHONHOME -u PYTHONPATH .venv/bin/python -m pytest tests/`）
3. **CLI 实测**（8002 端口新代码实例）：
   - `--file` 与 `--log-file-path` 同传 → `Error: use only one of --file or --log-file-path`，exit=2
   - 本条日志即以 `--log-file-path` 瘦身形态追加，响应含 `similarity_skipped: slim form`、`log.file_path` 透出（detail/logs 读路径可见）

## 4. 备注

- ruff check 改动文件全过；新测试文件已 format；既有 6 文件的 format 差异为 HEAD 基线状态（stash 对比确认，非本次引入）
- alembic 045 迁移 SIM102 已修（合并嵌套 if），未向既有债务（M59-2 范畴）新增条目
- 8001 旧实例未动（避免打断既有会话依赖）；实测走 8002 同代码同 DB 实例
