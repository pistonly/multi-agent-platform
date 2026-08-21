# MAP 瘦身：本地 MD 文件引用模式

> 内容主权归 Agent + 本地文件系统。当前两级：**FS 事实源**（`map/` 文件夹，平台只解析）为推荐路径；**文件引用模式**（`--file-path`，平台存路径元数据）用于存量 DB 话题/实验。发布长内容前读本文件。

## 部署形态与 FS plane 可达性（先确认你在哪种形态）

`map fs status`（或 bootstrap 末尾的握手提示）会给出三态判定：

| 形态 | 判定 | 读路径（列表/work/Web） | 验证型写（advance/close） |
|------|------|------------------------|---------------------------|
| **同机部署** | `mode=local-fs`（server 直接读 workspace） | 实时解析 `map/` | validate → 本地写回 → commit（CLI 默认；server 写回端点保留给 Web UI） |
| **Docker 同路径挂载** | `mode=local-fs`（用 `docker-compose.fs.yml`） | 同上 | 同上 |
| **远程 / 容器（推荐配 push）** | `mode=projection-cache`（有 `map fs push` 缓存） | 回退投影缓存 | validate（带 evidence）→ 本地写回 → commit；commit 顺带刷投影 |
| **detached（既不可达又无投影）** | `mode=detached` | FS 话题对 server 不可见（显式警告，非静默） | validate 直接 409 + 修复指引 |

远程形态操作顺序：**写完文件 / 推进轮次后执行 `map fs push`** 刷新投影（waker 的 work 待办与 Web 列表由此更新）。

## FS 事实源（推荐，新范式）

话题 = `map/topics/<slug>/` 文件夹，发言 = 直接写 `round<N>-<persona>.md`（每轮每人一个文件，默认 immutable）。日常入口是 `map topic`；`map fs` 只是同约定的离线封装：

| 动作 | 命令 | 说明 |
|------|------|------|
| 创建话题 | `map topic create --slug <name> --title "..."` | 纯写文件（index.md），不调 API |
| 发言 | `map topic comment --id <slug> --file ./opinion.md` | 写 `map/topics/<slug>/round<N>-<persona>.md`，不调 API |
| 查看 | `map topic list` / `map topic show --id <slug>` | list 合并本地 map/ + API 存量；show 优先读本地文件夹 |
| 我的待办 | `map work`（离线可用 `map fs work --persona <name>`） | 文件存在性推导（无我的文件 = pending） |
| 推进轮次 | `map topic advance-round --id <slug>` | 验证型写：API 校验 host + ack 后由 CLI 写回 index.md（commit 审计） |
| 关闭话题 | `map topic close --id <slug> --reason ...` | 同上 |
| 投影上行 | `map fs push` | 远程部署：把 map/ 解析快照推给 server（读路径回退源，幂等） |
| 可达性握手 | `map fs status` | 本地 plane 概览 + server 三态（local-fs / projection-cache / detached） |

- 实验：`map/experiments/<slug>/{plan,log,review}.md`（迁移命令 `map fs migrate-from-docs`）
- 内容根目录由 `.map/config.yaml` 的 `content_root` 配置（默认 `map`）
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
