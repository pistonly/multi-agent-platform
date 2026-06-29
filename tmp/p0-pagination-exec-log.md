# Web UI 列表分页 + 筛选（P0）执行日志

## 变更文件

| 文件 | 变更 |
|------|------|
| `web/src/api/client.ts` | 新增 `PaginatedResult<T>`；`fetchTopics` / `fetchProjectExperiments` 支持 status/phase/q/page/pageSize，从 `X-Total-Count` 解析 total |
| `web/src/components/PaginationBar.tsx` | 新增通用分页条（共 N 条 · 第 x/y 页 + 上/下页） |
| `web/src/components/ProjectStatusPanel.tsx` | 「话题」卡片：status Tab + 搜索防抖 + 分页；「实验列表」卡片：phase 筛选 + 搜索 + 分页 API；快照区 `open_topics` / `active_experiments` 不变 |
| `web/src/components/CreateExperimentForm.tsx` | 创建实验后 invalidate `project-experiments` 查询 |

## 验收

- [x] 话题列表 status 筛选（全部/进行中/已关闭）
- [x] 实验列表 phase 筛选
- [x] 两列表关键词搜索（300ms 防抖）
- [x] 分页翻页，total 来自 `X-Total-Count`
- [x] 快照区行为不变
- [x] `npm run build`（web）通过
