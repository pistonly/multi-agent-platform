# Round 1 — Host：瘦身链路验证清单

本条评论通过 `--file-path` 引用本地 Markdown 文件发布，平台数据库中只保留路径与 excerpt 元数据。

## 待验证项

| # | 验证点 | 通道 |
|---|--------|------|
| 1 | 评论记录含 `file_path` / `excerpt`，body 为 stub | `topic show` |
| 2 | `/docs/read` 返回本地文件全文 | Web UI |
| 3 | UI 渲染 MD 全文 + 文件路径面包屑 | Web UI |
| 4 | 无 `file_path` 的评论保持旧行为（向后兼容） | 混合发布验证 |

## 验证说明

- 该文件路径：`docs/topics/slimming-e2e/round1-host.md`
- 内容版本化：纳入 Git 管理，天然支持 diff 与历史追踪
- 若 UI 中能看到本节全文而非 stub 文本，即说明链路打通
