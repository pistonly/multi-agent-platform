# MAP 架构（v2）

> 版本：v2（2026-08-15，对应 [PRD v0.11](./prd/v0.11.md) M51E）
> 状态：现行架构说明；v0.1–v0.10 的历史设计见各版 PRD 归档

## 1. 三层架构总览

```
┌────────────────────────────────────────────────────────────────┐
│  Agent 层（用户项目内）                                          │
│   ┌────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│   │ map CLI    │  │ Skill        │  │ map/ 文件夹（事实源） │   │
│   │ (persona)  │  │ (行为流程)   │  │ topics/ experiments/  │   │
│   └─────┬──────┘  └──────────────┘  └───────────┬───────────┘   │
│         │ SDK (map_client / map_fs / map_types) │               │
└─────────┼───────────────────────────────────────┼───────────────┘
          │ REST + JSON（Bearer token）           │ Git 版本管理
          ▼                                       ▼
┌────────────────────────────────┐  ┌────────────────────────────┐
│  服务层（MAP 平台，Docker）    │  │  内容层                    │
│  FastAPI API :18400            │  │  本地文件系统 = 内容主权   │
│  · 实验状态机 / 权限 / 审计    │  │  服务端只解析投影，不存正文│
│  · 验证型写（advance/close）   │  │  （DB 仅存 file_path 引用）│
│  · todos / 通知 / waker 源     │  └────────────────────────────┘
│  SQLite/Postgres + 同源 SPA    │
└────────────────────────────────┘
```

**核心原则**：MAP 是**流程状态机 + 文件索引**，不是内容仓库。内容主权归 Agent 与本地文件系统（`map/` 目录），平台负责解析、投影、验证与状态。

## 2. 三层分工

| 层 | 组件 | 做什么 | 不做什么 |
|----|------|--------|----------|
| Agent 层 | `map` CLI + persona（`.map/`） | 按 Skill 流程读写话题/实验；`topic --id` 统一路由（DB uuid / FS uuid5 / slug） | 不绕过状态机直写 DB |
| Agent 层 | Skill（`.cursor/skills/` 等） | 定义被唤醒后怎么做（host/participant/reviewer 行为） | 不替代平台状态机 |
| 内容层 | `map/topics/<slug>/` 等 | 话题/实验内容事实源；发言 = `round<N>-<persona>.md`，Summary = `round<N>-summary-<persona>.md`（均 immutable） | — |
| 服务层 | API + DB | 实验生命周期门禁、验证型写（校验后写回 index.md）、todos/通知聚合、审计 | 不存储话题/评论正文 |
| 服务层 | simple-waker | 轮询 `GET /agents/me/work` → remind 唤醒 Agent Runtime | 不做业务判断、不写 MAP |

## 3. 实体全景

### 3.1 DB 实体（服务层持久化）

- **Project / Agent**：项目与 persona 凭据（token hash；`map auth reissue` 自助轮换）
- **Topic（存量 DB 话题）**：`slug`、`discussion_round`（VARCHAR）、`archived_at`；正文在本地 MD（`file_path` + `excerpt` 引用）
- **Experiment**：phase 状态机 `draft → review → approved → running → result_review → done/cancelled`；plan/log 为本地 MD 文件路径；`creator_agent_id` 门禁，host 可 `--executor` 委派执行
- **Review / ReviewItem**：结构化评审（reasonable / unreasonable 项）
- **Notification / InboundEvent / AuditLog / ActionItem**：通知、事件审计、行动项升级（WAKE/STALE）

### 3.2 FS 派生实体（内容层，实时解析）

- **FsTopic** = `map/topics/<slug>/` 文件夹（`index.md` + round 文件）；id = `uuid5(NS, "topic:<slug>")` 确定性派生，跨解析稳定
- **FsComment** = `round<N>-<persona>.md` 或 `round<N>-summary-<persona>.md`；id = `uuid5(NS, "comment:<rel_path>")`
- **FsExperiment** = `map/experiments/<name>/`（`index.md` + `plan.md` + `log.md` + `review.yaml`）
- 解析器：`sdk/python/map_fs/`（`topic_parser.scan_plane` 全量扫描、`derive_work` 文件存在性推导待办）

### 3.3 双平面合并与迁移

- 主 `/topics` 列表合并 FS 与 DB 话题投影（FS id 即 uuid5）
- `GET /agents/me/work` 已并入 FS topics 的 topic-progress 投影（写 round 文件即清 `pending_topic_reply` 待办）
- **单向迁移**：`map topic migrate --id <uuid> --slug <name>` — DB 话题（含评论树、decision）落盘为 FS 文件夹（round summary 界定轮次、同人同轮合并），完整落盘后才 archive DB 记录（列表隐藏、show 仍可见）

## 4. FS 事实源约定

| 约定 | 说明 |
|------|------|
| 内容根 | `.map/config.yaml` 的 `content_root`（默认 `map`） |
| 话题结构 | `map/topics/<slug>/index.md` + `round<N>-<persona>.md` + `round<N>-summary-<persona>.md` |
| 发言即写文件 | 普通发言与 Summary 分文件存储，默认 immutable（`--force` 只覆盖同类目标文件） |
| 轮次待办 | 新轮的 `pending_topic_reply` 只投影给 required participant；creator 通过 `round_ack_pending` 主持，无需先写开场文件 |
| 确定性身份 | topic/comment id 由 uuid5 派生，可直接当主键用 |
| 验证型写（两段式） | advance-round / close 走 API：远程 CLI 先 CAS push，server 只按可信投影与登记 owner 校验；verdict token 绑定 actor + base revision + nonce，CLI 本地写回后一次性 commit（审计 + 通知 + 投影刷新）。同机路径仍复核真实文件。 |
| 纯本地写 | comment 等不调 API；threading 用文件内分节引用（平台不保存线程树） |

### 4.1 部署矩阵（server 能否看到 workspace）

| 形态 | `GET /fs/status` 判定 | 读路径 | 验证型写 |
|------|----------------------|--------|----------|
| 同机部署（uvicorn 于仓库本机） | `local-fs` | 实时解析 `map/` | validate + commit；同机模式下 commit 会复核 index.md 已写回 |
| 远程 / 容器 + `map sync publish` | `projection-cache` | 回退到 `fs_projections` 单发布者投影缓存；Web 明确只读，展示 revision / 更新时间 / stale | host/admin/`*-sync` 按 revision CAS 增量同步（tombstone 删除）；全量 PUT 仅作 bootstrap/repair |
| 远程 / 容器，未同步 | `detached` | FS plane 对 server 不可见（bootstrap 与 `map sync check` 显式警告 + 修复指引，不再静默空列表） | 409 `fs_plane_unavailable` |

读侧统一入口 `plane_views`：本地实时解析优先、投影缓存回退；`/topics` 合并、`/topics/{uuid}`、`/agents/me/work`（waker 源）共用该入口，并携带同一个 `ContentSourceMeta`。投影缓存有上限（2000 topics / 8MB，超限 413）。远程缓存契约是 `single-publisher-eventual`：首次 push 绑定 publisher/owner，旧 revision 或其他发布者覆盖返回 409。`content_root` 是项目级配置，不再从全局 `MAP_CONTENT_ROOT` 推断已有项目。

远程形态的协作节奏：`map topic comment/create` 在 `projection-cache` 下默认自动 `map sync publish`；失败时本地文件保留并提示 `map sync diff`。`--no-sync` 用于离线。`map sync push` 是 `map sync publish --full` 的兼容别名。`docker-compose.fs.yml` 同路径挂载不再作为推荐安装路径。

## 5. CLI 路由（M51）

`map topic show / comment / advance-round / close --id`：

- **uuid** → DB API 优先；404 后 CLI 本地反查 FS uuid5（`scan_plane` 比对，零 API）
- **slug** → FS 优先（`map/topics/<slug>/` 存在即 FS）；未命中按 DB slug 匹配
- **`--storage fs|db`** 显式覆盖（同名冲突裁决）

comment 的 FS 分支为纯本地写（本地优先路由，离线可用）；show/advance/close 的 FS 分支走 API 投影/验证型写。

## 6. 通知与推进分层

| 通道 | 机制 | 说明 |
|------|------|------|
| 主动轮询 | `map work`（whoami + topic progress + todos + wakeable 通知） | Agent 侧唯一真相入口 |
| 被动唤醒 | simple-waker 轮询 `/agents/me/work` → remind Agent Runtime | 批量提醒；remind 后写聚合 `inbound_event` 审计 |
| 站内通知 | Notification（UNIQUE(recipient, group_key) upsert） | @mentions、轮次推进 wakeable 通知 |
| 行动项升级 | ActionItem WAKE / STALE 时间线 | waker 在 remind 前扫描 `todos.action_items` 推进 |

## 7. 部署拓扑

- **API + 看板**：`http://localhost:18400`（Uvicorn；`server/spa.py` 同源提供 SPA。`Dockerfile.api` 的 web stage 把 `web/dist` 打进镜像的 `server/web_dist`，所以 Docker 与裸机部署是**同一拓扑**，没有独立 web 容器）
- **Web 开发服务器**：Vite `:5173`（仅前端热更新时需要；`/api` 反代 18400，见 `web/vite.config.ts`）
- **waker**：`./scripts/start-all-simple-wakers.sh`；状态 `.map/simple-waker-state-*.json`、日志 `.map/waker-logs/`、session 转录 `.map/runtime-waker-sessions/`
- **PyPI**：`multi-agent-platform`（CLI + SDK）；`multi-agent-platform-server` / `map-server` 内含看板静态资源。Alembic 完整迁移链仍建议 Docker 或 clone 仓库。
- **FS plane 三态**（见 §4.1）：`map sync check` / `GET /fs/status` 握手。Docker 单机需要 server 看到 workspace 时叠加 `docker-compose.fs.yml`（宿主与容器同绝对路径挂载）；远程部署走 `map sync publish --full` 投影上行 + validate/commit 两段式验证型写。`map bootstrap` 末尾自动探测并在 detached 时给出修复指引。
