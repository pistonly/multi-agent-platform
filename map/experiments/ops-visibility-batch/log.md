# 4770ea76 执行日志（ops-visibility-batch）

## summary

运维可见性第一批④：`map audit list --target` 与 `map topic history` 双入口落地。纯 CLI 层补口，`GET /admin/audit` 零改动。

## 实施 log

### I1 slug 解析器
- `cli/audit_target.py`：`--target` 解析话题 slug/uuid5 与实验 slug/uuid/shortid；实验优先探测，撞名列出两者 exit 2（不静默猜）。
- 实验 slug 来自 `plan_file_path` 的 `map/experiments/<slug>/` 段；shortid 走既有 `list_experiments_page(id_prefix=)`。
- `--project-root` 参与本地 FS 反查（find_map_dir）。

### I2 `map audit list --target`（C1+C3+C4）
- 有 `--target`：persona `_client_ctx` → `GET /audit`（非 admin），`--kind` 客户端过滤，`--limit`≤200。
- 无 `--target`：原 admin `GET /admin/audit` 路径不动；与 `--experiment` 互斥。
- 空结果打印 `No audit events for …`，不打空表头。

### I3 `map topic history`（C2+C3）
- 话题 audit + `experiment.topic_id` 匹配的实验 audit，时间倒序合并（naive/aware datetime 归一化）。
- FS 话题无 DB 行时 GET /audit 404 视为空话题事件，仍并入关联实验事件。

### I4 实测
- `map audit list --target 4770ea76`：三条 phase/created 时间线（03:10 / 02:59 / 02:55）。
- `map topic history --id ops-visibility-batch`：同一实验事件并入（FS 话题自身无 DB audit 行）。
- 撞名：`--target ops-visibility-batch` exit 2，列出 experiment 4770ea76 与 topic 58c5ccdd。

### 验证
- `tests/cli/test_ops_visibility_audit.py` 12 passed；compat + dry-run + 既有 admin CLI 回归绿。
- ruff 改动文件全绿。
- commit：`798c19d`

## 风险

- FS 话题 `ensure_topic_access` 查 DB Topic 表，无行则 GET /audit 404——CLI 降级为空列表，靠关联实验事件填话题 history。
- 实验/话题同 slug 时 `--target` 拒绝猜测，须用 uuid/shortid；`topic history --id` 走话题路由，不受撞名影响。
- list_experiments 扫一页 100 条匹配 slug，超页可能漏（当前项目量级可接受，plan 不做分页扩界）。

## acceptance

- C1 ✅ `--target` 自动识别话题/实验，走 GET /audit，可与 `--kind` 组合；live 4770ea76 输出三条事件
- C2 ✅ `topic history` 聚合话题+关联实验，时间倒序；live ops-visibility-batch 见实验事件
- C3 ✅ table/yaml/json；空结果友好提示（单测钉死）
- C4 ✅ 无 `--target` 仍要求 admin token；`server/api/audit.py` `/admin/audit` + require_admin 零改动
- C5 ✅ 只做④，未动 ①②③
- 测试面 ✅ 五组场景（解析/聚合/403/三格式/空结果）+ help/C4 共 12 passed；ruff 通过
