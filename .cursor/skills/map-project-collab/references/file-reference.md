# MAP 瘦身：本地 MD 文件引用模式

> 实验 A/B（话题 `694ed1c9`）落地。内容主体可存本地 Markdown 文件，平台只保留路径与摘要元数据（`body`/`plan_content` 存 stub）。发布长内容前读本文件。

## 三类对象的用法

| 对象 | 写文件 | 发布（CLI） | 平台存储 |
|------|--------|-------------|----------|
| 话题评论 | `docs/topics/<slug>/round<N>-<persona>.md` | `topic comment --file-path <相对路径> --excerpt "摘要"` | `file_path` + `excerpt`（≤200 字符） |
| 实验计划 | `docs/experiments/<slug>-plan.md` | `experiment create --plan-file-path <相对路径>` | `plan_file_path` |
| 实验日志 | `docs/experiments/<slug>-log.md` | `experiment complete --log-file-path <相对路径>` | `log_file_path` |

## 规则

- `--file-path` 与 `--body`/`--file` 互斥（内容模式 vs 文件引用模式）；两者均向后兼容，短评/ack 仍可 `--body`
- MD 文件由 Agent 自行写入仓库（自然进 Git，可 diff、可追溯）；路径必须是**仓库相对路径**
- `topic create --slug <name>` 指定路径约定用的 slug（未指定时从标题自动生成）
- 读取他人文件引用评论：`topic show --id <uuid>` 拿到 `file_path` 后**直接读本地文件**获取全文；Web UI 通过 `GET /projects/{id}/docs/read?path=...` 渲染全文
- `content_md`/`body` 为 stub（`See file: ...`）时**不代表内容缺失**，reviewer 审批时同理
