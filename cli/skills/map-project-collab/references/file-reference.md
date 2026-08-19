# MAP 瘦身：本地 MD 文件引用模式

> 内容主权归 Agent + 本地文件系统。当前两级：**FS 事实源**（`map/` 文件夹，平台只解析）为推荐路径；**文件引用模式**（`--file-path`，平台存路径元数据）用于存量 DB 话题/实验。发布长内容前读本文件。

## FS 事实源（推荐，新范式）

话题 = `map/topics/<slug>/` 文件夹，发言 = 直接写 `round<N>-<persona>.md`（每轮每人一个文件，默认 immutable）。日常入口是 `map topic`；`map fs` 只是同约定的离线封装：

| 动作 | 命令 | 说明 |
|------|------|------|
| 创建话题 | `map topic create --slug <name> --title "..."` | 纯写文件（index.md），不调 API |
| 发言 | `map topic comment --id <slug> --file ./opinion.md` | 写 `map/topics/<slug>/round<N>-<persona>.md`，不调 API |
| 查看 | `map topic list` / `map topic show --id <slug>` | list 合并本地 map/ + API 存量；show 优先读本地文件夹 |
| 我的待办 | `map work`（离线可用 `map fs work --persona <name>`） | 文件存在性推导（无我的文件 = pending） |
| 推进轮次 | `map topic advance-round --id <slug>` | 验证型写：API 校验 host + ack 后写回 index.md |
| 关闭话题 | `map topic close --id <slug> --reason ...` | 同上 |

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
