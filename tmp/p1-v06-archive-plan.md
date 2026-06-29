# 目标

v0.6 P1：话题/实验**归档** + **独立列表页** + Web `client.ts` 单元测试。

# 实施范围

## 后端

- `topics` / `experiments` 表新增 `archived_at`
- 列表 API 默认排除已归档；`include_archived=true` 可查
- PATCH `archived: true|false` 归档/取消归档
- 快照区（`open_topics` / `active_experiments` / `recent_experiments`）排除已归档
- 活跃实验 per-topic 唯一索引排除已归档实验

## Web

- `/projects/:id/topics`、`/projects/:id/experiments` 独立列表页
- 项目页列表区「查看全部 →」链接
- 列表「显示已归档」筛选；话题/实验详情页归档按钮
- `client.ts` vitest：`parseTotalCount`、分页 fetch 参数

# 验收

- [ ] 归档后默认列表不可见，`include_archived` 可见
- [ ] 归档活跃实验后可在同话题再开新实验
- [ ] 独立列表页可访问
- [ ] `npm run test` + `npm run build` 通过
- [ ] pytest 通过
