# 优化任务清单

> 来源：2026-08-25 对 `server/`、`cli/`、`sdk/`、`tests/`、`web/`、CI 与打包配置的三路静态审查（约 9.7 万行 Python）。
> 用法：完成一项把 `[ ]` 改成 `[x]`；涉及服务端行为的改动跑 `pytest` 验证（快测用 `./scripts/test-fast.sh`）。
> 预估口径：小 = 半天内｜中 = 1-3 天｜大 = 超过 3 天。

统计：P0 × 5｜P1 × 27｜P2 × 12，共 44 项。**P0 已全部完成（2026-08-25）**，验证：ruff + alembic 001→051 + 相关测试 83 通过。**P1 已全部完成 27/27（2026-09-01，T24 收官）**：T06-T23、T25-T27、T29（2026-08-25/26）与 T28/T30/T31/T32（2026-09-01）+ T24 分两批（2026-08-26 热路径 / 2026-09-01 e2e 迁移 + deprecated 标记）。P2 已完成 8/12：T39（2026-08-27）、T34/T38（2026-09-01 上午批）、T40/T41/T42/T43（2026-09-01 下午批）与 T33（2026-09-01）；T24 2/2 验证：ruff 全绿 + fast gate 1945 passed 50s（T24 测试扩至 16 例）。

## P0 性能与正确性热点（已完成 2026-08-25）

- [x] **T01 认证改等值查询，消除每请求 bcrypt**（预估：中）✅ 2026-08-25
  落地：`agents.api_token_sha256` 列（迁移 051，唯一索引）；`create_agent`/`bootstrap`/`reissue_agent_token` 同时写入 bcrypt + sha256；`get_agent_by_token` 先走 sha256 等值查找，未命中才进 legacy bcrypt 路径并在成功后惰性回填。测试 `test_get_agent_by_token_fast_path_avoids_bcrypt` 断言快路径零 bcrypt。
  位置：`server/services/auth.py` → `get_agent_by_token`（L50-66，经 `server/api/deps.py` 作用于全部受保护端点）。
  问题：每次 API 请求都跑 `bcrypt.checkpw`（约 200-300ms）；token 本身是 256-bit 随机数，不存在暴力破解面，慢哈希属过度防御。simple-waker 每 30s 轮询 `/agents/me/work`，全部命中此成本。
  改法：新增 `api_token_hash = sha256(token)` 列并建唯一索引，等值查询直取；或保留 bcrypt 但加进程内短 TTL 的 token → agent_id 缓存。涉及迁移与 reissue 兼容时参考 037（webhook secret 加密）的回填模式。

- [x] **T02 收敛 legacy 空 prefix 回退的 bcrypt 放大**（预估：小，依赖 T01 决策）✅ 2026-08-25
  落地：legacy bcrypt 路径仅扫 `api_token_sha256 IS NULL` 的行（集合随惰性回填单调收缩到空）；空 prefix 扫描加 `LEGACY_CANDIDATE_LIMIT=256` 硬上限，命中上限未匹配时告警提示 reissue。
  位置：`server/services/auth.py` L62-65。
  问题：prefix 未命中时回退遍历 `api_token_prefix == ""` 的全部 legacy agent 逐个 bcrypt——攻击者发随机 token 即可让服务端做 N 次慢哈希，是 CPU DoS 放大点。
  改法：给 legacy 候选集设上限；或跑一次性迁移强制 legacy agent reissue token 后删除回退分支。若 T01 落地则本项自然消失。

- [x] **T03 降低 simple-waker 每周期子进程税**（预估：中）✅ 2026-08-25（近期项）
  落地：`_ensure_identity` 优先取 `work` 快照的 `agent` 字段（老 server/测试 mock 回退 whoami）；身份缓存到 `self._me`，action_item escalation 不再二次调 whoami 子进程。scan-stalled → work 顺序保持不变。中期项（waker 迁 SDK in-process）归 T24。
  位置：`cli/simple_waker.py` → `_run_once_async`（L659-744）+ `cli/map_command_client.py`。
  问题：每周期起 5+ 个 `map` 子进程（whoami、work、remind、逐条 mark-wake-sent/mark-stale、inbound_event_record），每个都是完整 Python + Typer + pydantic 冷启动；3 个 persona 常驻运行。
  改法（两步）：近期把 identity 从 `work` 快照取（省 whoami 子进程）；中期让 waker 直接复用 SDK `MAPClient` in-process 调用（见 T24）。

- [x] **T04 补根目录与 web/ 的 .dockerignore**（预估：小）✅ 2026-08-25
  落地：根 `.dockerignore` 排除 .git/node_modules/构建产物/.map 密钥目录及镜像不需要的仓库内容；`web/.dockerignore` 排除 node_modules/dist/coverage，堵住宿主 node_modules 覆盖容器 `npm ci` 依赖的问题。
  位置：仓库根、`web/` 均无 `.dockerignore`。
  问题：根 build context 会把 `.git`（约 17MB）、node_modules、dist/build、`.venv` 送入 daemon；更严重的是 `web/Dockerfile` 的 `COPY . .` 会用宿主 node_modules 覆盖容器内 `npm ci` 装好的依赖，存在平台/架构不一致的正确性风险。
  改法：两处各加 `.dockerignore`，至少排除 `.git`、`node_modules`、`dist`、`build`、`.venv`、`*.egg-info`、`web/dist`。

- [x] **T05 CI backend job 加依赖缓存**（预估：小）✅ 2026-08-25
  落地：`pip install uv` 换 `astral-sh/setup-uv@v5`（pin 0.8.14 不变，`enable-cache: true` + `cache-dependency-glob: uv.lock`），三个 matrix leg 的依赖包走缓存恢复。
  位置：`.github/workflows/ci.yml` backend job（L28-43）。
  问题：`pip install uv` + `uv sync --frozen` 无任何缓存（setup-python 未开 cache，也无 actions/cache），3 个 Python 版本 matrix 每次全量下载依赖。
  改法：改用 `astral-sh/setup-uv` 并开 `enable-cache`（lockfile key）；或 setup-python `cache: pip` + actions/cache 缓存 uv 目录。

## P1 服务端

- [x] **T06 拆分 fs_source_service.py（1729 行上帝模块）**（预估：大）✅ 2026-08-26（本批完成局部拆分 + complete_experiment）
  落地：投影缓存存储簇（`upsert_fs_projection`/`apply_fs_projection_delta`/`get_fs_projection`/`projection_*` 系列，~514 行）拆至 `server/services/fs_projection_store.py`，原模块 re-export 维持既有导入路径（1729 → 1215 行）；`phase_service.complete_experiment`（137 行）拆为 `_build_completion_log` / `_complete_direct_mode` / `_complete_standard_mode` 三个职责单一辅助。fs_scan / fs_topic_view / fs_lifecycle_ops 的进一步四分未做——投影簇已消除最大耦合块，剩余职责边界清晰度可接受，留待模块再度膨胀时再拆。

- [x] **T07 消除 get_todos mention 分支双重 N+1**（预估：中）✅ 2026-08-25
  落地：`thread_activity` 拆出纯评估 `_evaluated_comment_after` 并新增 `replied_after_batch`（按容器一条 IN 预取评论，同容器多 mention 共享一次加载，单条路径与批量路径共享同一判定语义）；`mention_service.agents_replied_after_mentions` 提供批量包装；`todo_service` mention 循环改候选过滤 + 批量回复判定 + `_agent_names_by_ids`（复用 topic_helpers 既有实现）批量取 author 名；`list_pending_plan_revisions` 循环内逐实验 count 改 `open_status_unreasonable_count_by_experiment` 一条 GROUP BY。测试含批量 vs 单条六场景逐项 parity 断言 + SQL 守卫。
  位置：`server/services/todo_service.py` L509-532。
  问题：循环内每个 mention 各跑一次 `agent_replied_after_mention` 查询和一次 `Agent.name` 查询；`get_todos` 是 waker 每 tick 的核心路径。同文件 L413-427 的 log 批量化是现成范式。
  改法：author_name 用 `IN` 批量预取，回复判断合并为一次批量查询。同类型的 `list_pending_plan_revisions`（L310-323 循环内 count）一并处理。

- [x] **T08 webhook 投递移出长事务**（预估：中）✅ 2026-08-26
  落地：`_perform_delivery` 重试循环内每次尝试后 `db.commit()`（原 `flush` 不 commit），sleep 与下次 HTTP 尝试期间事务不再持有 SQLite 写锁；投递参数在循环外捕获为本地值，规避 mid-loop commit 导致的 ORM expire。调用方（通知扇出）进入前已完成自身 commit，无嵌套事务问题。

- [x] **T09 topics 列表 FS 合并路径去掉 DB 全量拉取**（预估：中）✅ 2026-08-25
  落地：`list_topics` 新增 `exclude_slugs`（NULL slug 显式放行，规避 NOT IN 三值逻辑）与 `offset` 参数；`api/topics.py` 合并分支改为分段分页——FS 段占合并视图前缀、DB 段以 `offset = max(0, start - fs_count)` 续页，`X-Total-Count = fs_count + DB total`。语义与旧内存合并一致（既有分页稳定性测试全过），SQL 守卫断言取行查询必带 LIMIT、无全量拉取。

- [x] **T10 /status 聚合看板加短缓存**（预估：小）✅ 2026-08-25
  落地：`status_service` 加进程内 TTL 缓存（`MAP_STATUS_CACHE_TTL_SECONDS`，默认 5s，0 关闭），原构建逻辑改名 `_build_global_status`；按 project_id 分键、线程锁保护；conftest 增加 autouse `reset_status_cache` fixture 隔离测试。测试断言 TTL 内命中零 SQL、过期/reset 重建、TTL=0 关闭、per-project 键独立。
  位置：`server/api/status.py` + `server/services/status_service.py`。
  问题：每次调用做跨项目聚合计数 + recent10 + 全部 agent 心跳扫描，Web 看板每次刷新都触发，无缓存无 ETag。
  改法：5-10s 进程内 TTL 缓存或 ETag/304。

- [x] **T11 notifications 加复合索引并评估裁剪单列索引**（预估：小）✅ 2026-08-25
  落地：迁移 052 新增 `ix_notifications_recipient_updated(recipient_agent_id, updated_at)` 服务 `list_for_agent` 排序分页热点（EXPLAIN 守卫验证走索引、无全量 filesort）；裁剪 5 个冗余单列索引（fingerprint_version 无查询过滤、category/read_at 仅与 recipient 联合出现、group_key 由 038 唯一约束覆盖、recipient_agent_id 由复合索引左前缀覆盖），created_at 保留。顺带修复存量漂移：ORM 声明的 `ix_notifications_project_id` 历史迁移从未建过，052 补建。ORM 模型同步移除 5 列 `index=True` 并在 `__table_args__` 声明复合索引，测试守卫断言 create_all 库与迁移库索引集一致。
  位置：`server/domain/models.py` L638-683 + `server/services/notification_service.py` L607-615，需新迁移。
  问题：`list_for_agent` 按 `(recipient_agent_id, updated_at DESC)` 排序分页但只有单列索引，大表 filesort；表上单列索引偏多造成写放大。
  改法：加 `(recipient_agent_id, updated_at)` 复合索引，评估裁剪低基数单列索引。

- [x] **T12 SSE 换 asyncio.Queue，不再占线程池线程**（预估：中）✅ 2026-08-25
  落地：`notification_stream` 传输改 `asyncio.Queue` + `asyncio.wait_for` 超时心跳，SSE 连接不再占 anyio 线程池线程；订阅者记录所属事件循环，同步请求线程的 publish 经 `loop.call_soon_threadsafe` 桥接回目标循环（无 loop 的测试订阅者退化为直接 put）。新增跨线程桥接测试（事件循环内订阅 + 普通线程 publish 可被 await 到）；既有 SSE 测试改用 get_nowait 轮询辅助，隔离扫描（仅允许 stdlib + fastapi/starlette 导入）保持通过。

- [x] **T13 mark_all_read 改单条 UPDATE**（预估：小）✅ 2026-08-25
  落地：`notification_service.mark_all_read` 改单条核心 UPDATE + rowcount，替换「加载全部未读 ORM 对象逐行赋值」（原 N 行 = 1 SELECT + N UPDATE，现恒定 1 UPDATE）。回归测试用 SQL 计数守卫断言任意行数下恰好 1 条 UPDATE、0 条 SELECT。
  位置：`server/services/notification_service.py` L686-700。
  问题：加载全部未读 Notification 对象逐行赋值 `read_at`，未读量大时内存与 SQL 双放大。
  改法：单条 `UPDATE notifications SET read_at=:now WHERE recipient_agent_id=:x AND read_at IS NULL`，用 rowcount 确认。

- [x] **T14 mention 已读同步批量化**（预估：小）✅ 2026-08-25
  落地：`mark_agent_mentioned_notifications_read_no_commit` 去重 (mentioned_agent_id, source_id) 对后合成单条核心 UPDATE（`event=agent.mentioned AND 未读 AND (recipient, target) 任一匹配`），行数取 rowcount；原先 M 个 mention = M SELECT + K 逐行 UPDATE。保持 no-commit 语义与调用方事务边界一致。SQL 守卫断言任意 mention 数下恰好 1 UPDATE、0 SELECT。
  位置：`server/services/notification_service.py` L641-670 → `mark_agent_mentioned_notifications_read_no_commit`。
  问题：每个 mention 一条 SELECT 再逐行更新。
  改法：合并为按 `(recipient, event, target_id IN (...))` 的批量查询 + 批量 UPDATE。

- [x] **T15 stalled-lock 扫描消 N+1**（预估：小）✅ 2026-08-25
  落地：`notify_stalled_experiment_locks` 重构为两段式——Pass 1 纯 Python 按 progress_threshold 过滤候选，`_latest_experiment_log_at_batch` 一条分组 IN 查询取齐全部候选的 max(created_at)（替代逐实验 SELECT）；project agent 列表每 project 只查一次，holder 排除改在 Python 侧（缓存无排除原始列表）。`_project_agent_ids` 保留原两步写法（mypy strict 守卫测试钉住）。SQL 守卫：N 候选日志查询恒 1 条、agent 查询每 project 1 条。
  位置：`server/services/notification_service.py` L852-946 → `notify_stalled_experiment_locks`。
  问题：循环内每个 experiment 各调 `_latest_experiment_log_at`（L838）与 `_project_agent_ids`（L844）。
  改法：`experiment_id IN (...)` 一次取 `max(created_at)` 分组结果；agent 列表每 project 缓存一次。

- [x] **T16 projects status 的 recent 改 SQL 侧限量**（预估：中）✅ 2026-08-25
  落地：`build_projects_status` 的 recent 查询改 `row_number() OVER (PARTITION BY project_id ORDER BY updated_at DESC)` 窗口函数 + `rn <= 5`，每项目第 6 名起不离开数据库（原为全量拉回内存逐项目截断）。测试断言每项目恰 5 条最新（含 deleted/archived 过滤不变）+ SQL 守卫（窗口语句恰 1 条、无无 LIMIT 的全量行查询）。
  位置：`server/services/project_service.py` L190-203 → `build_projects_status`。
  问题：拉取所有项目的全部未删实验再在 Python 中截取每项目 5 条，实验数增长后退化为全表载入。
  改法：窗口函数 `row_number() over (partition by project_id order by updated_at desc)` 或每项目 LIMIT N 的 UNION。

- [x] **T17 中型文件按域拆分**（预估：中）✅ 2026-08-26（models.py 部分经评估定为非紧急，明确不做）
  落地 1/2——notification_service（1000→852 行）：stalled-lock 扫描簇（`notify_stalled_experiment_locks` + 3 个私有辅助，~155 行）拆至 `server/services/notification_stalled.py`，原模块顶部 re-export 维持既有导入路径；fanout 依赖经函数内 lazy import 反向引用，无导入环。
  落地 2/2——`server/api/experiments.py`（888 → 274 行，18+ 路由按生命周期拆）：M3 执行域（start/complete/accept-result/reject-result、logs、cross-persona-call、CP-3 执行锁）拆至 `server/api/experiment_execution.py`（430 行）；plans/reviews/comments（含 review-items PATCH）拆至 `server/api/experiment_reviews.py`（251 行）；experiments.py 仅留 CRUD + 相位流转，经 `include_router` 聚合子路由——`experiments_router` 导入路径与全部 URL 契约不变（OpenAPI 路径集逐一核对）。
  未做：`server/domain/models.py`（837 行）拆分——纯声明式 ORM 模型、无逻辑耦合，拆分收益低，维持现状。

- [x] **T18 FS 扫描加 mtime/revision 级缓存**（预估：中）✅ 2026-08-26
  落地：`fs_source_service` 增加进程内 plane 缓存——以 `(workspace, root_name)` 为键，缓存值为完整目录指纹（topics/ + experiments/ 下全部文件的 `(relpath, mtime_ns, size, ino)`）与解析出的 `FsPlane`；任何写路径（CLI 落盘 / 验证型写回 / Agent 直接编辑）改变 mtime、size 或 inode 即指纹失配、自动重扫。同大小覆盖写入在粗粒度时间戳 FS 上只靠 mtime 会漏失效，故指纹含 `st_ino`。LRU 上限防多 workspace 膨胀，`fs_plane_cache_enabled` 设置可关（默认开），`reset_plane_cache()` 供测试隔离。测试 `tests/test_optimization_t18.py` 覆盖命中零重扫 / 写入失效 / 关闭开关直通。

- [x] **T19 宽捕获补日志、时区工具去重**（预估：小）✅ 2026-08-26
  落地：宽捕获路径补 `logger.warning`（fs_source_service 快照降级等 6 处，附异常摘要便于排障）；时区辅助收敛至 `server/services/time_utils.as_utc`（overload 保证 strict-mypy 下 `datetime -> datetime` 精确），`notification_stalled._aware` 与 `status_service` / `todo_service` / `topic_ack_service` 的 `_as_utc` 三处雷同实现统一替换。

## P1 CLI 与 SDK

- [x] **T20 删除 participant/reviewer bridge 死代码**（预估：小）✅ 2026-08-26
  落地：删除 `cli/participant_worker.py`、`cli/reviewer_worker.py`、2 个 console entry、4 个 `start-*-bridge*.sh` stub 与 2 个配套测试；LEGACY-ENTRY-MATRIX.md 增「已退役（T20）」条目并清空 DEPRECATED 在册清单（check-deprecated.sh 通过）；README 退役章节同步。共享模块 `bridge_state.py` / `worker_cycle_log.py` 为 simple-waker/orchestrator 主路径使用，保留。

- [x] **T21 host_worker_types.py 只留 WorkerError**（预估：小）✅ 2026-08-26
  落地：`WorkerError` 迁至新建 `cli/errors.py`，删除 `cli/host_worker_types.py` 全文（`MapClientProtocol` 40+ 行、`WorkerConfig`、`WorkerStats`、`DEFAULT_REPLY_TEMPLATE` 均零外部引用）；9 个引用方（simple_waker / runtime_chat / map_command_client / e2e_collab / wake_backend / bridge_state + 3 测试）改从 `cli.errors` 导入。

- [x] **T22 wake 超时后复位 Agent SDK 连接**（预估：小）✅ 2026-08-26
  落地：`wake_up` 的 TimeoutError 分支在 break 前补 `await self.disconnect()`——旧 client 的 `receive_response()` 流可能仍挂起，`_connected` 残留 True 会让下一轮复用同一 client 卡在同一流上。state 的 session id 保留（resume 语义延续对话上下文；彻底弃 session 走 `wake_backend.reset_session()` 由调用方决定）。两个超时测试补断言：`disconnect_calls == 1`、`_connected is False`、`_client is None`。

- [x] **T23 打破 main ↔ commands 循环 import**（预估：中）✅ 2026-08-26
  落地：新建 `cli/runner.py`（`_run` 执行链、client ctx、JSON error envelope、序列化、`_resolve_project` / creator / executor / `_require_*` / `_load_topic_resolve_payload`）与 `cli/io_helpers.py`（`_read_text_file` / `_read_yaml_file`，原定义在 experiment.py 又被 main re-export 回去）。commands / audit_target / persona_compare / fs_projection 的 40+ 处函数内 lazy import 改顶层导入；main.py 1591 → 970 行（size-cap 守卫 1600 内），保留 re-export 兼容层与 `_cli_options` / `_transport` 状态。
  关键设计：`_run` / `_resolve_project` 经 `runner._xxx` 模块属性调用（而非 from-import 固化绑定），runner 内部对 `_client_ctx` / `resolve_client` / `admin_client` / `find_map_dir` / `load_project_map_config` 运行时经 `cli.main` 解析——保住测试的全部 monkeypatch 注入面（`cli.main._transport` / `cli.main._client_ctx` / `cli.main.resolve_client` / `cli.runner._run` 等）；4 个测试的 patch 目标同步迁移（shortid / json_schema / m55 / notification_bulk_filter / fs_projection_cli）。剩余函数内 lazy import 仅 `_cli_options` / `_transport` / `_cli_version` / `_project_cli_default_format` 运行时状态（monkeypatch 面，按设计保留）。验证：全量 1580 passed；slow 门控的 test_cli.py 8 个失败经 HEAD 基线对比确认为既存（SOCKS 代理环境 + ReviewCreate 等，非本次引入）。

- [x] **T24 waker 迁移到 SDK，收敛双客户端层**（预估：大，T03 的中期项）✅ 2026-09-01（分两批）
  落地 1/2（2026-08-26）：`simple-waker` 默认走 `cli/map_sdk_client.py`（in-process `MAPClient`，`work`/`whoami`/lock scan/inbound-event/mark-wake|stale 不再起 `map` 子进程）；`--subprocess-client` 与 `MAP_WAKER_SUBPROCESS=1` 回退。
  落地 2/2（2026-09-01）：①`MapSdkClient` 补 e2e 面 3 个读方法——`topic_show`（server 端已合并 FS uuid5 与 DB）、`experiment_list`（lazy import 复用 `cli.experiment_fs` 合并编排，本地 map/experiments 显示权威不变）、`experiment_status`（复用 `_load_experiment`：DB GET + FS overlay + 404 index.md 合成）；②`cli/e2e_collab.py` 整体迁 `MapSdkClient`，run 收尾补 `map_host.close()`；③`MapCommandClient` 标记 deprecated（docstring + LEGACY-ENTRY-MATRIX v1.0 移除时间表登记，保留用途：waker `--subprocess-client` 回退 + 测试注入面）；④顺手删 `run_lock.MapClientLockBackend`（host bridge 退役后零引用的死适配器，且耦合 deprecated 类）。`--dry-run` 写拦截按 1/2 已落地的 per-method bool 拦截（waker 面方法全覆盖；e2e 纯只读无需拦截）。测试 `tests/test_optimization_t24.py` 扩至 16 例。
  说明：任务原文「orchestrator 走 subprocess 层」已过时——orchestrator（`cli/orchestrator.py`）只用 `PersonaAgentClient` 唤醒 Agent，从不直接读 MAP；e2e 才是最后一个 subprocess 消费方。

- [x] **T25 inbound_event_record 去重 _run 逻辑**（预估：小）✅ 2026-08-26
  落地：`MapCommandClient._run` 增加 ``map_exit``（把特定非零退出码映射成返回值）；`inbound_event_record` 用 `{0: True, 2: False}` 表达 409→CLI exit 2 的去重语义，timeout/错误拼装与 dry-run 登记复用 `_run`，删除约 40 行复制品。测试 `tests/test_optimization_t25.py`。
  位置：`cli/map_command_client.py`。

- [x] **T26 合并 _resolve_creator/executor_agent_id**（预估：小）✅ 2026-08-26
  落地：抽 ``_resolve_agent_ref(client, project_id, value, *, flag, label)``（UUID 直通 → list_agents 过滤 → 0 命中列出 → 多命中报歧义）；``_resolve_executor_agent_id`` 只剩一行委托，``_resolve_creator_agent_id`` 保留双 flag 别名/冲突门禁后走同一查找。位置在 T23 后的 `cli/runner.py`（不再是 main.py）。测试 `tests/test_optimization_t26.py`。

- [x] **T27 收窄 SDK bootstrap 的宽泛捕获**（预估：小）✅ 2026-08-26
  落地：`bootstrap.py` 8 处 `except Exception` 按场景收窄为 YAML 探测 `(YAMLError, OSError, ValueError)`、HTTP 建连 `(HTTPError, OSError, ValueError, ImportError)`、请求 `(RequestError, OSError)`、错误体 JSON `(ValueError, TypeError)`，并 `logger.debug` 留痕。CLI：`fs_projection.maybe_auto_sync` / `warn_fs_plane_detached` 的静默 skip 与 `runner._resolve_project` 的 `load_config` 探测同步收窄，TypeError 等程序 bug 不再当「离线/缺配置」。测试 `tests/test_optimization_t27.py`。

- [x] **T28 SDK 加连接级重试**（预估：小）✅ 2026-09-01
  落地：`MAPClient.__init__` 新增 `retries: int = 1`（连接级，传 0 关闭）——transport 未显式指定时默认创建 `httpx.HTTPTransport(retries=retries, trust_env=not _is_local_url(base_url))`，「本地地址忽略环境代理、远端沿用」原语义不变（trust_env 同步下传 Client 与 HTTPTransport）；调用方显式传 transport 时不包装、`close()` 仍不代管关闭。测试 `tests/test_optimization_t28.py`（6 case：默认 retries=1 / retries=0 / 本地 trust_env=False / 远端 True / 自定义 transport 不包装 ×2）。全量快测 1957 passed 无回归。
  位置：`sdk/python/map_client/client.py` L134-140、L171-204。
  问题：`httpx.Client` 未配 retries，连接拒绝/瞬时 5xx 直接抛出，长流程被迫在 subprocess 层自建重试。
  改法：`httpx.HTTPTransport(retries=1)`（连接级）或可选的请求级 retry 参数。

## P1 打包与 CI

- [x] **T29 修复 wheel 打包缺口并加冒烟校验**（预估：中）✅ 2026-08-26
  落地：MANIFEST `graft alembic` + `include alembic.ini`；`alembic.ini` 的 `script_location` 改为 `%(here)s/alembic`（复制进 wheel 后仍指向同目录脚本树）。setuptools 只打包包内文件，故 PEP 517 后端 `scripts/map_build_backend.py` 在 sdist/wheel 前把根目录 alembic 复制到 `server/_migrate`（gitignore，不进 `alembic/__init__.py` 以免 shadow 第三方包）。`python -m server.migrate` 优先 cwd `alembic.ini`，否则用 wheel 内副本。CI `packaging` job：`sync-web-dist.sh` + `check-packaging.sh` 解包断言 `web_dist/assets` 非空且 `_migrate/alembic/versions` 存在。测试 `tests/test_optimization_t29.py`。
  位置：`MANIFEST.in` + `pyproject.toml`。
  问题：`web_dist` 构建产物被 gitignore 且被 global-exclude 排除，干净 checkout 打的 sdist/wheel 没有 Web UI；`alembic/`、`alembic.ini` 不在 MANIFEST，wheel 用户无法跑迁移（Dockerfile 手动 COPY 掩盖了问题）。
  改法：MANIFEST 增加 `graft alembic`、`include alembic.ini`；CI 加 release job：构建 wheel → 解包检查 web_dist/assets 非空 + alembic 存在；本地打包前跑 `scripts/sync-web-dist.sh`。

- [x] **T30 CI 拆独立 lint job**（预估：小）✅ 2026-09-01
  落地：`ci.yml` 新增单版本（Python 3.12）`lint` job 承接 check-deprecated / ruff / mypy strict / alembic 四类确定性检查（沿用 setup-uv 缓存与 editable 安装）；`backend` job 保留 3.10/3.11/3.12 矩阵只跑 pytest——lint 类步骤从 ×3 降为 ×1。YAML 结构经本地解析校验。
  位置：`.github/workflows/ci.yml` L23-59。
  问题：ruff/mypy/alembic 检查/check-deprecated 在 3.10/3.11/3.12 三个 leg 各跑一遍，lint 类步骤 ×3 浪费。
  改法：lint 拆单版本独立 job，test matrix 只跑 pytest。

- [x] **T31 untrack 编译产物与运行时状态文件**（预估：小）✅ 2026-09-01
  落地：`git rm --cached` 三个文件 + `.gitignore` 分节排除；本地陈旧的 `web/vite.config.js`/`.d.ts` 副本一并删除（`tsc -b` 经 composite 的 `tsconfig.node.json` 每次 `npm run build` 会再生成，且 vite 解析 `vite.config.js` 优先于 `.ts`，保留陈旧副本有配置漂移风险；删后 dev 直读 `.ts`）。`.claude/skills` 保持跟踪。`git status` 仅剩本次任务变更。
  位置：`web/vite.config.js`、`web/vite.config.d.ts`（tsc 编译产物，会漂移）；`.claude/topic_watch_state.json`（运行时状态，"巡检已停止"）。
  改法：`git rm --cached` + `.gitignore` 排除；保留 `.claude/skills` 跟踪。

- [x] **T32 Dockerfile 镜像源 ARG 化 + 非 root 运行**（预估：小）✅ 2026-09-01
  落地：Dockerfile.api / Dockerfile.mcp 新增 `ARG PIP_INDEX_URL`（默认空=官方源，国内构建传 `--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple`，`--timeout 120 --retries 5` 保留）；`ARG UID/GID=1000` 创建 map 用户（api 侧 `chown -R map:map /app/data` 后 `USER map`，mcp 侧无持久写入统一安全基线）。docker-compose.fs.yml 的 build.args 透传 `MAP_UID/MAP_GID`（默认 1000，建议 `export MAP_UID=$(id -u) MAP_GID=$(id -g)` 对齐宿主），原「root-owned 文件」已知权衡改为 UID/GID 对齐方案。未动 web/Dockerfile（nginx 基础镜像，不在本项位置清单内）。
  位置：`Dockerfile.api` L17-18、`Dockerfile.mcp` L6-7。

## P2 可排期项

- [x] **T33 超大文件与长函数拆分**（预估：大）✅ 2026-09-01
  落地：`cli/main.py` 970→638 行——`map_dashboard`→`cli/dashboard.py`、clear-action 渲染→`cli/work_render.py`、n2 子命令格式化块→`cli/subcommand_format.py`；`MAX_CLI_MAIN_PY_LINES` 硬上限按任务要求 1600→800（test_compat 守卫）。`cli/commands/topic.py` 1737→1148 行——话题路由 helpers→`cli/topic_routing.py`、action_item 子应用→`cli/commands/action_item.py`。`cli/commands/experiment.py` 1573→1423 行——review/plan 子应用→`cli/commands/experiment_review.py`：宿主模块底部注册 apps 打破循环导入，`_ID_HELP`（装饰期求值）保持模块级导入，`_rid`/`_run_lifecycle` 等 monkeypatch 面留守宿主并在命令体内 call-time lazy 导入（T23 模式）。`skill_install`/`skill_upgrade` 共享 preamble 抽为 `_skill_cmd_preamble` + `_select_skill_candidates` 两个 helper（skill.py 624→617 行）。`cli/commands/` 新增 2 模块已登记 test_compat 目录清单。验证：ruff 全绿 + fast gate 1945 passed 52s。

- [x] **T34 tests 加速与覆盖率可见性**（预估：中）✅ 2026-09-01
  落地：`pytest-xdist>=3.6` dev 依赖 + `-n auto` 并行（`scripts/test-fast.sh` / CI PR gate / nightly 全量；本地实测 1933 例 213s→44s）；PR gate 加 `--cov --cov-report=term` 展示 unit subset 覆盖率（不 fail_under——unit 覆盖率不代表整体，全量门禁仍走 nightly → Codecov）；修复 CI 上游连红：`uv sync` 后 `.venv/bin` 不在 PATH，裸调 ruff/mypy/pytest 全部 exit 127，lint 从未真正执行（两 job 各注入 `$GITHUB_PATH`）；`tests/conftest.py` 新增 autouse `reset_cli_options`——CLI 测试改写 `cli.main._cli_options` 泄漏全局态，xdist worker 分组下人类可读断言随机失败；顺带清 ruff 积压（B904/E402/SIM108/I001/F541 等）。**降级**：204 文件按域分子目录机械量大、git 历史噪声高且收益低（pytest 已按 marker 分层），独立为将来可选重构，不再列待办。

- [ ] **T35 alembic 迁移 squash（v1.0 时机）**（预估：中）
  50 个迁移中约 20 个不足 60 行（011/018/019/020/028/030/040/041/045/049/050 等）。仅在有存量部署评估后于大版本做一次基线重建 + 数据校验脚本；有生产库在跑则保持现状，迁移历史即部署历史。

- [ ] **T36 前端依赖升级**（预估：中）
  vite 5 / vitest 2 / tailwind 3 均落后一个大版本；`@types/diff@^7` 与运行时 `diff@^9` 类型错位。非紧急，排期升级。

- [ ] **T37 web 大文件拆分**（预估：中）
  `web/src/pages/TopicPage.tsx`（646 行）拆子区块；`web/src/api/client.ts`（593 行）按资源域拆模块。

- [x] **T38 一次性产物归档**（预估：小）✅ 2026-09-01
  落地：`scripts/migrate_action_items_closed_topics.py`、`reports/` 3 个 e2e 报告、6 个退役 bridge runner（`claude-*-runner.py` / `cursor-*-runner.py`）移入 `scripts/archive/`（报告在 `scripts/archive/reports/`），grep 确认无代码引用（conftest 注释与 `docs/MAP-AGENT-RUNTIME-UNIFIED.md` 的路径引用同步）；`map/` 存量归档：HEAD 时 `map/topics/` 剩余 12 个 closed 话题（waker-status/cost-ledger 系列、pending-review-routing-deadlock 等）全部移入 `map/archive/topics/`（35→47），`map/archive/INDEX.md` 重建；`map fs verify-audit` 归档后 clean（0 drift）。后续新增 closed 话题按 `docs/PROJECT-ARCHIVE-PLAN.md` 定期归档。

- [x] **T39 waker 退避与优雅退出**（预估：小）
  `cli/simple_waker.py` 固定 300s 重试无指数退避；`run_forever` 无 SIGTERM handler，`backend.disconnect()` 不保证执行。已按连续失败次数指数退避（idle×2^n，cap 30min）+ 注册 signal handler（SIGTERM/SIGINT 置位 + `asyncio.Event` 唤醒睡眠，finally 统一 disconnect）。补充 8 个专项测试 `tests/test_simple_waker_backoff_graceful.py`；顺带修复 Py3.10 下 `asyncio.wait_for` 超时抛 `asyncio.TimeoutError`（3.11 前与内置 `TimeoutError` 非同一类型）导致退避睡眠未被捕获的 bug。

- [x] **T40 依赖与入口清理**（预估：小）✅ 2026-09-01
  落地：dev 组 httpx 冗余声明删除（核心 dependencies 已含）；`mcp` extra 移除 `multi-agent-platform[server]`——经 import 链核查 map_mcp 全模块零 server 依赖（httpx/pydantic/typer 来自核心依赖，starlette 随 mcp 装），原拆 `server-core` 方案直接升级为"不需要"，Dockerfile.mcp Layer 1 依赖清单同步去掉 server extra（fastapi/sqlalchemy/alembic 等不再进镜像，显著减重；server/ 源码仍 COPY 仅为 wheel 打包完整性）；deprecated entry points 1.0 时间表评估完成——console scripts 层零 deprecated（3 入口全主路径），warning 级兼容面（`--format legacy`、2 个 JSON 别名、API `page_size`）在 LEGACY-ENTRY-MATRIX.md 登记「连续两个 minor 无使用告警即移除，最迟 v1.0」。uv.lock 手工同步（requires-dist + optional-dependencies）。
  验证：ruff 全绿 + fast gate 1937 passed（含 test_mcp 全套）。

- [x] **T41 API 一致性小项**（预估：小）✅ 2026-09-01
  落地：`/agents`、`/webhooks`、`/projects` 三个列表端点补齐 `page`/`page_size` 参数 + `X-Total-Count` 响应头（与 experiments/topics/audit 风格对齐）；`agents.py` 两处 category 归一化抽为 `_normalize_notification_category` helper，422 报错带上参数名；SDK `list_agents`/`list_webhooks`/`list_projects` 透传分页参数；`status_service` 看板聚合显式传 `page_size=None` 取全量。专项测试 `tests/test_optimization_t41.py` 4 例（分页 + total 头 + 422 文案）。

- [x] **T42 CLI 渲染与 envelope 统一**（预估：小）✅ 2026-09-01
  落地：`cli/runner.py` 新增 `emit_json_success`（统一 `{"ok": true, "data": ...}` envelope），`_run` 与 `skill.py`/`auth.py` 手拼 JSON 共 5 处收敛到该 helper；`skill list` 手写固定宽度列改用 `cli/table_render.render_table`（表头统一大写风格），`test_skill_install.py` 断言同步更新。

- [x] **T43 零散死代码清理**（预估：小）✅ 2026-09-01
  落地：①`cli/main.py` 兼容 re-export 清理——评审时点的「`_transport` 死参数」已过时（现为 32 处测试 monkeypatch 注入面，保留），改为删除 map_sdk.evidence / persona_compare / io_helpers 三个 re-export 块 + runner 块 24 名中 17 个纯死名，保留 main.py 自用 5 名（其中 `_client_ctx`/`_admin_client_ctx` 兼注入面）；6 个测试文件迁直接导入（runner/persona_compare/experiment/map_sdk.evidence）。②`cli/wake_backend.py` 删 runtime-waker 遗留的 `_todo_item_stable_id`/`_my_open_experiment_wake_stable_id`（生产零引用，唯一消费方是一个 noqa F401 死 import）。③`cli/simple_waker.py` `summarize_pending_work` lambda 别名改 def。④`cli/agent_client.py` rc 凭证解析由 key×文件逐次读盘（5 key×4 文件=20 次）改为每文件 `parse_export_env_file` 解析一次、实例内缓存（顺带删除重复实现的 `_read_export`）。另修复既有坏点：`tests/test_cli.py` 两处 `cli_main.ReviewCreate` AttributeError（slow 层测试，slow tier 不跑故未暴露）。
  验证：ruff 全绿 + fast gate 1940 passed 49s（新增专项测试 `tests/test_optimization_t43.py` 3 例）+ 受影响 slow 测试 167 passed（`test_18a64691_linkage` 3 例失败为改动前既有，需 live server，已 stash 复核确认）。

- [ ] **T44 本地与仓库卫生**（预估：小）⏳ 2026-09-01 落地 1/2
  落地：`build/`、`dist/`（0.8.0 过期产物）、`multi_agent_platform.egg-info/` 本地清理完成。`reference/noise_solver_agent_claudecode` 复查实为无 .gitmodules 的悬空 gitlink（mode 160000、本地目录已空、代码与 CI 零引用，仅 `map/archive/topics/` 归档讨论提及该 persona 名），按用户决定整体移除（untrack + 删空目录 + gitignore `reference/` 防误提交）。`git gc`、`test_project/` 迁移仍为可选项。
  `build/`、`dist/`（过期 0.8.0 产物，当前 0.9.1）、`*.egg-info/` 均已被 gitignore，本地清理即可；偶跑 `git gc --prune=now`（实测 2055 loose objects）；`reference/noise_solver_agent_claudecode` 确认是否仍需跟踪；`test_project/` 是测试 fixture 保留，可选迁 `tests/fixtures/`。

## 审查确认无需改动

以下经审查确认质量良好，优化时不要顺手重构：

- 错误链路：`MAPHTTPError` 携带 error_code/hint/retryable/recovery_command，JSON envelope 经 pydantic 锁 schema，STATE_MACHINE 错误追加 Escalation 联系人。
- 测试基建：核心 fixture 全部集中在 `tests/conftest.py` 无重复；in-memory sqlite + savepoint 回滚；slow/integration marker 体系完善。
- 数据库会话：`server/db/session.py` 标准 sessionmaker + FastAPI 依赖，无每请求建 engine；todo/log/review 服务已有批量预取与 joinedload 治理。
- 安全：agent token 无明文列；webhook secret Fernet 落盘加密（迁移 037 已回填），密钥缺失显式抛错；无 SQL 注入面。
- alembic 链：001→050 严格线性无分叉；`verify_schema_matches_models` 启动校验、SSE Last-Event-ID replay、deadlock_retry 均为高质量实践。
- git 仓库：size-pack 4.61 MiB，无二进制大文件入库。
- TODO/FIXME：server/ 与 cli/、sdk/ 零残留，历史决策均有 commit/experiment 编号注释。
