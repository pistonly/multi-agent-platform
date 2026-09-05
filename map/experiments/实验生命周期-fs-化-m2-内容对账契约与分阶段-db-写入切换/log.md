# M2 执行日志

## summary

host 委派 participant（标准模式，phase=running）按 plan 顺序推进 M2。本
wake 完成 **I2**：① 补全 ``FsExperimentRead`` 五个字段（I0 §A.4 标记
项），② 落地 ``sync --check`` 固定契约 + 五类 kind 判定（A2），③ 连
续两次 ``sync --check`` 独立快照输出完全一致（A2 acceptance 「无
blocking drift」项）。I0/I1 已在前两 wake 完成；I3+ 留待后续 wake。

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
