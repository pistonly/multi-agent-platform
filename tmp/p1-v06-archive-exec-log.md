# v0.6 P1 执行日志

## 变更

| 层 | 文件 |
|----|------|
| DB | `alembic/versions/013_topic_experiment_archived.py` |
| 后端 | models, topic_service, project_service, todos, topics/experiments API |
| Web | TopicsListPanel, ExperimentsListPanel, ProjectTopics/ExperimentsPage, 归档 UI |
| 测试 | `tests/test_topics.py` 归档用例；`web/src/api/client.test.ts` |

## 验收

- [x] 归档默认隐藏 + include_archived
- [x] 归档后同话题可再开实验
- [x] `/projects/:id/topics` `/projects/:id/experiments`
- [x] vitest 5 passed, web build OK
- [x] pytest 104 passed（含 test_cli_status 版本断言修复）
