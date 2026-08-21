# MAP 瘦身：本地 MD 文件引用模式

> 内容主权归 Agent + 本地文件系统。当前两级：**FS 事实源**（`map/` 文件夹，平台只解析）为推荐路径；**文件引用模式**（`--file-path`，平台存路径元数据）用于存量 DB 话题/实验。发布长内容前读本文件。

## 部署形态与 FS plane 可达性（先确认你在哪种形态）

`map fs status`（或 bootstrap 末尾的握手提示）会给出三态判定：

| 形态 | 判定 | 读路径（列表/work/Web） | 验证型写（advance/close） |
|------|------|------------------------|---------------------------|
| **同机部署** | `mode=local-fs`（server 直接读 workspace） | 实时解析 `map/` | validate → 本地写回 → commit（CLI 默认；server 写回端点保留给 Web UI） |
| **远程 / 容器（单发布者缓存）** | `mode=projection-cache`（有 `map fs sync` 缓存） | 回退投影缓存；Web 只读，展示 revision / stale | CLI 增量 CAS sync → server 以可信投影校验 → 本地写回 → 一次性 commit |
| **detached（既不可达又无投影）** | `mode=detached` | FS 话题对 server 不可见（显式警告，非静默） | validate 直接 409 + 修复指引 |

远程投影是 **trusted single publisher + eventual consistency** 兼容层，不支持多个 clone 各自全量覆盖。首次成功 sync 会绑定发布者；后续按 `projection_revision` CAS，旧 clone 返回 409。只有 project host、admin 或显式 `*-sync` agent 可以发布。`map topic create/comment` 在远程模式下默认自动 `map fs sync`（`--no-sync` 可关）；失败时本地文件保留，命令提示 `map fs diff` / `map fs sync`。删除远端对象必须显式 tombstone（`--yes`）。`map fs push` 是 `map fs sync --full` 的兼容别名。不要把 `docker-compose.fs.yml` 挂载当作推荐安装路径。

## FS 事实源（推荐，新范式）

话题 = `map/topics/<slug>/` 文件夹，发言 = 直接写 `round<N>-<persona>.md`（每轮每人一个文件，默认 immutable）。日常入口是 `map topic`；`map fs` 只是同约定的离线封装：

| 动作 | 命令 | 说明 |
|------|------|------|
| 创建话题 | `map topic create --slug <name> --title "..."` | 写 index.md；远程模式下默认自动 sync |
| 发言 | `map topic comment --id <slug> --file ./opinion.md` | 写 round 文件；远程模式下默认自动 sync（`--no-sync` 可关） |
| 查看 | `map topic list` / `map topic show --id <slug>` | list 合并本地 map/ + API 存量；show 优先读本地文件夹 |
| 我的待办 | `map work`（离线可用 `map fs work --persona <name>`） | 文件存在性推导（无我的文件 = pending） |
| 推进轮次 | `map topic advance-round --id <slug>` | 验证型写：API 校验 host + ack 后由 CLI 写回 index.md（commit 审计） |
| 关闭话题 | `map topic close --id <slug> --reason ...` | 同上 |
| 投影同步 | `map fs sync` / `map fs diff` / `map fs status` | 远程部署：增量 CAS + 显式 tombstone；`push` 为 `--full` 兼容别名 |
| 可达性握手 | `map fs status` | 本地 hash + server revision/publisher + in-sync/local-ahead/divergent/detached/stale |

- 实验：`map/experiments/<slug>/{plan,log,review}.md`（迁移命令 `map fs migrate-from-docs`）
- 内容根目录由项目级 `content_root`（bootstrap 写入 `.map/config.yaml`，默认 `map`）决定；CLI 本地扫描读 `.map/config.yaml`，服务端扫描读 Project 行，二者必须一致，否则 `map fs sync` 返回 409。
- FS 话题的 topic_id 为 uuid5 派生，可直接用于 `map topic --id`（slug 或 uuid5）

## 文件引用模式（存量 DB 话题/实验）

| 对象 | 写文件 | 发布（CLI） | 平台存储 |
|------|--------|-------------|----------|
| 话题评论 | `map/topics/<slug>/round<N>-<persona>.md` | `topic comment --file-path <相对路径> --excerpt "摘要"` | `file_path` + `excerpt`（≤200 字符） |
| 实验计划 | `map/experiments/<slug>/plan.md` | `experiment create --plan-file-path <相对路径>` | `plan_file_path` |
| 实验日志 | `map/experiments/<slug>/log.md` | `experiment complete --log-file-path <相对路径>` | `log_file_path` |

## 规则

- `--file-path` 与 `--body`/`--file` 互斥（内容模式 vs 文件引用模式）；短评/ack 仍可 `--body`
- MD 文件由 Agent 自行写入仓库（自然进 Git，可 diff、可追溯）；路径必须是**仓库相对路径**
- 读取他人文件引用评论：`topic show --id <uuid>` 拿到 `file_path` 后**直接读本地文件**；Web UI 通过 `GET /projects/{id}/docs/read?path=...` 渲染全文
- `content_md`/`body` 为 stub（`See file: ...`）时**不代表内容缺失**，reviewer 审批时同理
- 历史路径 `docs/topics/`、`docs/experiments/` 的存量内容已迁移至 `map/`（2026-08-15）；遇到旧路径引用按 `map/topics/...` 对应读取
