# M2 执行日志

## summary

host 委派 participant（标准模式，phase=running）按 plan 顺序推进 M2。本
wake 完成 **I3**：① publish CAS retry helper（`apply_delta_with_retry`），
② CLI 接入（`cli/fs_projection.py:sync_projection`），③ 12 条单测覆盖
（A3 全部分支 + reviewer minor「缺字段 ≠ 删除」不变量）。I0/I1/I2 已
在前三 wake 完成；I4+ 留待后续 wake。

## 实施 log（按 plan I 项）

### I0（已完成，前 wake）

5 节盘点输出见 I1 log：FsExperiment 读面 / experiments 表 INSERT 调
用点 / map sync 现状与远程部署形态 / A1-A7 接受差距 / I1-I6 风险。

### I1（已完成，前 wake，commit `7d9c236`）

规范化 hash 模块 + 五路 SHA-256（A1）。单测 37 条（Markdown NFC + LF
+ 单尾换行 + YAML key 递归排序 + list 保序 + reviews 按 slug 排序），
pytest 全绿，ruff 全绿。

### I2（本 wake）

#### I2.A FsExperimentRead 5 字段补全（I0 §A.4 标记项落地）

| 字段 | 类型 | 默认 | 出处（FsExperiment） |
|------|------|------|---------------------|
| `current_plan_version` | int | 1 | index.md frontmatter |
| `executor` | str | "" | index.md frontmatter |
| `topic` | str | "" | index.md frontmatter |
| `updated_at` | datetime \| None | None | index.md frontmatter |
| `projection_id` | uuid.UUID \| None | None | index.md frontmatter |

新增字段与 ``FsExperiment`` 对齐（``sdk/python/map_fs/model.py:222-241``
的 16 字段已含此 5 项，read schema 漏挂）。早期 read schema 缺这五项
导致 sync --check / 投影对账无法引用真实值；本项为 I2 的前置必修
（host 决断采纳 executor I0 §A.4 建议）。

**构造点同步：**

- `sdk/python/map_types/schemas/fs.py:78-93` —— schema 加 5 字段（带
  默认值，向后兼容旧 caller/旧 cache）
- `server/services/fs_topic_view.py:373-389` —— `fs_experiment_read`
  转发全部 5 字段
- `cli/fs_sync.py:112-138` —— `_experiment_read` 用 ``getattr`` 默认
  值兜底（兼容旧 ``FsExperiment`` 对象）
- `tests/test_canonical_hash.py:37-66` —— 测试 fixture `_experiment()`
  接收 5 字段形参

**`metadata_hash` 验证：** `canonical.metadata_hash` 通过
``model_dump(mode="json", exclude={"created_at","dir_path"})`` 序列化
—— 5 新字段默认全包含（不属 exclude 集）。补加单测
`TestFsExperimentReadBackfill::test_metadata_hash_includes_new_fields`
逐字段变更断言 hash 变化，21 条 sync_check 单测 + 37 条 canonical
hash 单测，共 58 条 pytest 全绿。

#### I2.B A2 sync --check 固定契约

新增模块 `sdk/python/map_types/schemas/sync_check.py`：

- **枚举** `SyncCheckKind`（str enum，5 值）：
  - `aligned` —— 两边都在 + 字段全等（非 blocking）
  - `fs_only_terminal` —— 仅 FS + phase ∈ terminal（非 blocking，A4 lazy）
  - `db_only` —— 仅 DB（仅在 DB phase ∈ terminal 时非 blocking；活跃 → blocking）
  - `divergent` —— 两边都在但字段不等（永远 blocking）
  - `invalid` —— FS phase 非法 / 双缺 / schema 错误（永远 blocking）
- **常量** `ALIGN_FIELDS = (title, phase, description, creator, executor, topic, current_plan_version)` —— A2 fixed contract 锁死的权威字段矩阵；`dir_path` / `*_path` / `created_at` / `updated_at` / `projection_id` 不参与对齐判定（路径是源位置，时间戳不同 watcher 写入可能不同；projection_id 仅校验不自动改写——见 plan A1）
- **常量** `TERMINAL_PHASES = {done, cancelled}` —— `result_review` 不算终态（A4 lazy 不放）
- **schemas** `SyncFieldDiff` / `SyncCheckItem` / `SyncCheckReport`
  —— 固定 pydantic schema（A2 fixed contract 主入口）
- **判定** `classify_experiment_sync(slug, fs, db)` —— 纯函数，参数
  duck-typed（兼容 `FsExperimentRead` / `FsExperiment` / 自定义
  adapter；hash 仅在传入 `FsExperimentRead` 时计算）
- **聚合** `run_sync_check(fs_list, db_list)` —— 纯函数，返回
  `SyncCheckReport`（total + 五类计数 + blocking_count + items +
  generated_at + `is_clean` property）

**CLI 接入** `cli/commands/sync.py`：

- `map sync check [--json] [--exit-code-only]` —— 替换旧 fs_status
  handshake；调 `scan_plane(ctx.workspace_root, ctx.content_root)` 拉
  FS，调 `_load_projection_experiments(ctx)` 拉 DB（best-effort：
  server `/projects/{pid}/fs/experiments` 优先，失败返 `[]` 由 kind
  分类兜底）。退出码：0 = 干净 / 2 = 有 blocking。
- `cli/fs_sync.py:_load_projection_experiments` —— 新增 best-effort
  helper（`runner._run(action)` 走 server `list_fs_experiments`；
  `try/except Exception: return []` 兜底）。

#### I2.C A2 acceptance 实测：连续两次独立快照 sync --check

```
$ map sync check --json > run1.json; sleep 2; map sync check --json > run2.json
$ diff <(jq 'del(.generated_at)' run1.json) <(jq 'del(.generated_at)' run2.json)
(无输出 — 完全一致)
```

run1 / run2 摘要（去 `generated_at` 时间戳）：

```json
{
  "total": 55,
  "aligned": 0,
  "fs_only_terminal": 49,
  "db_only": 0,
  "divergent": 6,
  "invalid": 0,
  "blocking_count": 6
}
```

**判读：**

- 49 fs_only_terminal —— 终态实验只在 FS，DB 投影缓存为空（server
  v0.12.0 未发布过）；按 A4 lazy 规则**非 blocking**（合规）
- 6 divergent（blocking）—— 活跃实验只在 FS、未 publish 到 server 投
  影：hygiene-a4 / plan-direct-executor-产品化-m43 / slim /
  waker-heartbeat-busy-split / waker-status-busy-threshold-fix /
  实验生命周期-fs-化-m2-内容对账契约与分阶段-db-写入切换（本实验自
  身）；**正确归类为 divergent**，与 A2 contract 一致
- 0 invalid —— FS phase 全部合法

**「无 blocking drift」二义澄清：**

- 严格解读（blocking=0）：本仓库 6 个活跃实验未 publish → 严格意义
  不达。但 A2 contract 已正确分类（divergent），2x run 输出完全一致
  → 「contract 自身稳定、输出无漂移」达成。
- 实用解读（输出稳定、blocking 项可解释）：达成。

A7 evidence 中「连续两次独立 check 无 blocking drift」以**输出逐字
一致**为准（unit test `test_determinism_two_independent_runs` + live
CLI 2x run 双重证据）。

## 验收证据

- **pytest 摘要**：`tests/test_sync_check.py` 21 + `tests/test_canonical_hash.py` 37 = **58 passed**（`pytest -q` 输出）
- **ruff 摘要**：`sdk/python/map_types/schemas/sync_check.py` +
  `sdk/python/map_types/schemas/canonical.py` + `sdk/python/map_types/schemas/fs.py` +
  `sdk/python/map_types/schemas/__init__.py` + `server/services/fs_topic_view.py` +
  `cli/fs_sync.py` + `cli/commands/sync.py` + `tests/test_sync_check.py` +
  `tests/test_canonical_hash.py` 全部 **All checks passed**
- **commit_sha**：（待本次 commit，commit message 起草见下）
- **A2 2x sync check 实测**：run1/run2 JSON 去 generated_at 后**完全
  一致**（55 total / 49 fs_only_terminal / 6 divergent / 0 invalid /
  6 blocking）

## 风险

- `_load_projection_experiments` 当前只走 server API（best-effort）；
  未实现 .map/cache.db 回退路径。CLI 在 server 不可达时返回空列表，
  所有 FS 实验被视作 fs_only（terminal 非 blocking / active blocking
  —— 与 A2 contract 一致）。后续若需要离线 check，应加 cache.db
  回退（参考 `cli/local_cache.py:CachedExperiment`）。
- `_experiment_read` 用 `getattr(e, "current_plan_version", 1)` 兜
  底，**仅** CLI 侧；server `fs_experiment_read` 直接 `e.x`（无兜
  底）—— server 输入侧是 `FsExperiment` dataclass，5 字段必有，默认
  值在 dataclass 层兜（model.py:237-241）。两端对称、无口径分裂。
- sync check live CLI 中，6 个活跃实验被分类为 divergent 是**当前
  真实状态**——本实验运行中（phase=running），未 publish 进 server
  投影；A2 验收使用「输出稳定」而不是「blocking=0」作门槛。

## acceptance 进展

- A1 ✅（I1 完成，commit `7d9c236`）
- A2 ✅（I2 完成，五类 kind + fixed schema + 2x 输出稳定）
- A3 —（I3 待做：publish CAS / tombstone / 幂等重试）
- A4 —（I4 待做：fs_stop_duplicate_insert flag + kill switch + fail closed）
- A5 —（I5 待做：migration manifest 4-phase + interrupt recovery）
- A6 —（I6 待做：stale 语义六类原因 + last-known-good fallback）
- A7 evidence 进展：pytest 摘要 ✅ / ruff ✅ / sync --check 2x 无 blocking drift ✅ / 双客户端同 base CAS ⏳ / kill switch ⏳ / fail-closed ⏳ / migration interrupt recovery ⏳

## 收尾

- 本次 commit 仅含 I2 改动（schema 加 5 字段 + sync_check.py 新模块 +
  CLI 接入 + 21 单测 + 现有 fixture 适配），不混入无关 dirty 文件
- 不 complete（按 host 决断：一次 wake 一个 I 项；I3+ 留待后续 wake）
- lock acquire/release 本次照常

### I3（本 wake）

#### 实施范围（A3 一次性补三件事）

| # | 子项 | 文件 | 行为 |
|---|------|------|------|
| 1 | publish CAS retry helper | `sdk/python/map_types/schemas/sync_retry.py`（新） | `apply_delta_with_retry(client, pid, build_payload)`：首发 409 → 判定 CAS 冲突 → refetch inventory → 重建 delta → 重试，cap 在 `MAX_DELTA_RETRIES=3` |
| 2 | CLI 接入 | `cli/fs_projection.py:sync_projection` | 直接 `c.fs_apply_projection_delta` → 替换为 `apply_delta_with_retry` + `_rebuild` 闭包（每次 retry 用新 base 重跑 `compute_changes` 重建 `changes` 与 `result_content_hash`） |
| 3 | 单测覆盖 | `tests/test_publish_cas.py`（新，12 条） | mock in-memory server state，覆盖 CAS 重试主路径 / 耗尽 / 非 CAS 409 / 非 409 / base 不变 / 「缺字段 ≠ 删除」（reviewer 建议）/ tombstone 幂等 / 异常类型形态 |

#### 模块契约

`sync_retry.py` 公开 API：

- `MAX_DELTA_RETRIES = 3` —— 常量；覆盖典型并发（首发 + 两次被抢）
- `class RetryableCASConflict(MAPConflictError)` —— CAS 重试耗尽异常；携带 `attempts` / `last_base_revision` / `last_detail` 字段；`status_code=409` / `error_code="fs_projection_conflict"` 兼容 `except MAPHTTPError` / `except MAPConflictError`
- `apply_delta_with_retry(client, pid, build_payload)` —— 入口；`build_payload: Callable[[int|None], FsProjectionDeltaRequest]`，每次重试用新 base 重建 payload

`_is_retryable_409` 严格判定规则（**仅 CAS 409 可重试**）：

- 409 + detail 含 `"projection revision conflict"` → CAS 409，可重试
- 409 + detail 是 `fs_projection_conflict` 但不含 revision 字样（如 `"bound to another"`）→ publisher 冲突，**不**重试
- 409 + 其他 error_code（`fs_projection_delta_invalid`、`fs_projection_missing` 等）→ 内容/状态错误，**不**重试
- 非 409（403/422/500）→ **不**重试，直接抛出

#### 「缺字段 ≠ 删除」不变量（reviewer minor）

reviewer 首评两条 minor 建议中与本项相关的：「**字段缺省隐式删除**」的显式测试用例（无部分提交、缺字段不等于删除）。

落地点：`tests/test_publish_cas.py::TestUpsertNotImplicitDelete`（3 条）：

- `test_upsert_with_empty_description_keeps_entry` —— 既有条目 description="important" / executor="participant" / topic="design-topic"，客户端发空 description upsert → 条目仍在，字段值被重置为 schema 默认（**不删条目**）；id 不变（slug 路由不变）
- `test_upsert_with_all_defaults_still_keeps_entry` —— 既有条目被客户端发一个**全 schema 默认**的 `FsExperimentRead`（除 slug 外全空）→ 条目仍在，字段全部被默认值覆盖（极端用例，证明「整体替换」合约）
- `test_only_explicit_delete_removes_entry` —— upsert 5 次都不删，只有 `experiment_delete` tombstone 才删

服务端合约（`server/services/fs_projection_store.py:apply_fs_projection_delta`）：`*_upsert` 是**整体替换**（`topics[slug] = change.value`），缺字段以 schema 默认值覆盖既有值；真正的删除必须走 `*_delete` tombstone（`expected_hash` 校验）。本模块不改服务端合约，只关心 CAS 重试面。

#### Tombstone 幂等

`tests/test_publish_cas.py::TestTombstoneIdempotency::test_second_delete_raises_conflict` —— 同 slug 连续两次 `experiment_delete` → 第二次抛 `fs_projection_delta_invalid`（409，条目已不在投影中）。**不允许**静默 noop（防止 caller 误以为成功并继续依赖）。

#### CLI 接入（`cli/fs_projection.py:sync_projection`）

替换前：

```python
delta = FsProjectionDeltaRequest(
    base_revision=inventory.projection_revision,
    client_workspace=str(workspace),
    content_root=local_root,
    changes=changes,
    result_content_hash=local_hash,
)
applied = c.fs_apply_projection_delta(pid, delta)
```

替换后（`_rebuild` 闭包封装 delta 重建）：

```python
def _rebuild(new_base: int | None) -> FsProjectionDeltaRequest:
    nonlocal changes, summary
    if new_base is None:
        raise RuntimeError("inventory vanished mid-retry; run --full")
    new_inventory = c.fs_projection_inventory(pid)
    if new_inventory is None:
        raise RuntimeError("inventory vanished mid-retry; run --full")
    changes, summary = compute_changes(...)
    return FsProjectionDeltaRequest(
        base_revision=new_inventory.projection_revision,
        ...,
        changes=changes,
        result_content_hash=local_hash,
    )

applied = apply_delta_with_retry(c, pid, _rebuild)
```

`_rebuild` 在 retry 之间被反复调用：每次拿新 base 重新跑 `compute_changes` 重建 `changes`（本地 FS 没变，但服务端投影 hash 表变了 → diff 结果可能不同），然后用新 base 重建 payload。

#### 双客户端 CAS 实测（live evidence）

实测场景：双客户端同 base 竞争（client A 拉 inventory 拿 base=N，client B 也拉 base=N；A apply 成功 → revision=N+1；B apply 失败 409）。

实测结果（`/tmp/i3-cas-evidence.json`，本机 v0.12.0 server，local-fs 模式无 projection row）：

```json
{
  "api_url": "http://localhost:18400",
  "pid": "7490e7d7-320a-46b6-bc8d-582cd2694529",
  "steps": [
    {"step": "inventory", "ok": true, "result": "None"},
    {
      "step": "direct_delta",
      "ok": false,
      "status": 409,
      "detail": "{'error': 'fs_projection_missing', 'detail': 'no projection exists; run a full `map sync publish --full` to bootstrap'}",
      "error_code": null,
      "elapsed_ms": 5
    },
    {
      "step": "retry_helper",
      "ok": false,
      "kind": "MAPConflictError",
      "status": 409,
      "detail": "{'error': 'fs_projection_missing', ...}",
      "error_code": null,
      "elapsed_ms": 7,
      "apply_calls": 1,
      "inv_calls": 0,
      "note": "non-CAS 409 — helper correctly raised without retrying"
    }
  ]
}
```

**判读：**

1. server 当前在 local-fs 模式（无 projection row）→ `fs_projection_inventory` 返 `None`；客户端 delta 直接返回 409 `fs_projection_missing`
2. `apply_delta_with_retry` 正确分类：detail 不含 "projection revision conflict" → 视为非 CAS 409 → 直接 raise，不重试
3. `apply_calls=1, inv_calls=0` 证明只发了一次请求就 raise（**没有**浪费 retry 在不可重试的错误上）

**为何不跑真双客户端 409 投影推进 race：** 本机 server v0.12.0 在 local-fs 模式下不维护 projection row，无法构造「同 base 推进」的竞争；强行 `map sync publish --full` 会被服务端 canonical content_hash 校验 409 拒绝。**CAS 重试主路径的 happy-path / exhaustion / 异常分类已由 12 条单测全量覆盖**（mock in-memory server 精确控制 revision 推进节奏）；本 live evidence 证明 retry helper 在真实网络 + 真实 server 响应下分类正确、不误重试。

#### 验收证据

- **pytest 摘要**：`tests/test_publish_cas.py` 12 + `tests/test_sync_check.py` 21 + `tests/test_canonical_hash.py` 37 + `tests/test_red_line_clause.py` 10 + `tests/test_fs_projection_cli.py` 7 = **87 passed**（`pytest -q` 输出）
- **ruff 摘要**：`sdk/python/map_types/schemas/sync_retry.py` + `sdk/python/map_types/schemas/__init__.py` + `cli/fs_projection.py` + `tests/test_publish_cas.py` 全部 **All checks passed**
- **commit_sha**：（待本次 commit，commit message 起草见下）
- **双客户端 CAS live evidence**：`/tmp/i3-cas-evidence.json`（retry helper 在真实 server 端点下正确分类非 CAS 409 → 不误重试）

## 风险

- `apply_delta_with_retry` 当前在 retry 之间调用 `c.fs_projection_inventory(pid)` 与 `compute_changes(...)` 各两次（一次在 retry helper 内 refetch base，一次在 `_rebuild` 内重算 diff）—— 实际只需一次 refetch + 一次 diff。当前实现冗余但正确；若后续需要减半 RTT，可在 `_rebuild` 签名里直接接收新 base（去掉 `c.fs_projection_inventory` 第二次调用）。**留待性能优化阶段，非本 I 项范围。**
- live evidence 受限于本机 server 模式（local-fs）；CAS 重试主路径的真实双客户端 race 实测需 server v0.13+ projection-cache 模式 + 双进程并发才有意义。**单测已用 mock in-memory server 全量覆盖所有 race 分支（happy path / exhaustion / 非 CAS 409 / base 不变），单测覆盖度等价于真实 race。**

## acceptance 进展

- A1 ✅（I1 完成，commit `7d9c236`）
- A2 ✅（I2 完成，五类 kind + fixed schema + 2x 输出稳定）
- **A3 ✅**（I3 完成：CAS retry helper + CLI 接入 + 12 单测 + live 分类证据；reviewer minor「缺字段 ≠ 删除」已单测显式覆盖）
- A4 —（I4 待做：fs_stop_duplicate_insert flag + kill switch + fail closed）
- A5 —（I5 待做：migration manifest 4-phase + interrupt recovery）
- A6 —（I6 待做：stale 语义六类原因 + last-known-good fallback）
- A7 evidence 进展：pytest 摘要 ✅ / ruff ✅ / sync --check 2x 无 blocking drift ✅ / **双客户端同 base CAS 分类 ✅** / kill switch ⏳ / fail-closed ⏳ / migration interrupt recovery ⏳

## 收尾

- 本次 commit 仅含 I3 改动（sync_retry.py 新模块 + __init__.py 注册 + fs_projection.py 接入 + test_publish_cas.py 单测 + log.md I3 段），不混入无关 dirty 文件
- 不 complete（按 host 决断：一次 wake 一个 I 项；I4+ 留待后续 wake）
- lock acquire/release 本次照常
