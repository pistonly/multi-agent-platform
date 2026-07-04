# Execution Log — topic dismiss

## 落地清单

### 数据层
- `server/domain/models.py` —— `Topic` 加 `dismissed_at` + `dismissed_by_agent_id`(FK agents.id);`creator` 关系加 `foreign_keys=[creator_agent_id]`(两 FK 后必要)
- `alembic/versions/019_topic_dismissed_at.py` —— SQLite batch 模式,FK 命名 `fk_topics_dismissed_by_agent_id`,分两个 batch 块避免 circular dependency

### Schema/SDK
- `sdk/python/map_types/schemas.py` —— `TopicSummaryRead` 加 `dismissed_at`

### 服务层
- `server/services/topic_service.py`:
  - `dismiss_topic(db, *, agent, topic_id)` —— host-only,幂等
  - `create_topic_comment` —— 写完评论后 bump `topic.updated_at`,触发"自动复活"语义
  - `topic_summaries_for_topics` 透传 `dismissed_at`

### Filter
- `server/services/todo_service.py` —— `my_open_topics` 加 `or_(dismissed_at.is_(None), Topic.updated_at > Topic.dismissed_at)`,被 dismiss 但有新动态的话题会重新出现

### API
- `server/api/topics.py` —— `POST /api/v1/topics/{topic_id}/dismiss` → `TopicSummaryRead`,非 host 返回 404

### Web
- `web/src/api/types.ts` —— `TopicSummary` 加 `dismissed_at`
- `web/src/api/client.ts` —— `dismissTopic(topicId)`
- `web/src/pages/TodosPage.tsx` —— my_open_topics 段每行加 `✕` 按钮,与 mention dismiss 同款 Section 复用

## 测试
新增 3 条:
- `test_dismiss_topic_hides_from_my_open_topics`
- `test_new_topic_comment_resurrects_dismissed_topic`
- `test_dismiss_topic_forbidden_for_non_creator`

## 验证
- 干净 SQLite 上 `alembic upgrade head` 014→019 round-trip 成功
- `test_todos.py` + `test_topics.py` + `test_mentions.py` = **36 passed in 126.74s**
- `web tsc -b` 干净
- 开发库 `data/map.db` 已 stamp 到 019,schema 一致

## 踩坑记录
- 第一次直写 `op.add_column` 加 FK 列 → SQLite 不支持 ALTER TABLE 加 FK
- 改 `batch_alter_table` 后,两列同时加 → 报 `CircularDependencyError`
- 分两个 batch 块 + FK 命名 → 干净通过
- 模型两 FK 都指向 agents 后,`creator` 关系需要显式 `foreign_keys=`
