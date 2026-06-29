# P1 v0.5.1 实施执行日志

## Part A — 话题评论通知（广播 + 主持定向）

- `notification_service.notify_topic_comment_created()`：项目广播（排除 actor 与主持），主持另收 `【主持】话题新评论待回复`（`host_directed: true`），无重复
- `topics.create_topic_comment`：`emit(notify=False)` + 专用通知逻辑
- `emit()` 增加 `notify` 参数

## Part B — topic_id 软校验

- `ExperimentSummaryRead.warnings: list[str]`
- `create_experiment_warnings()`：有 open 话题且未绑 topic_id → `["no_topic_id"]`
- CLI `_print_warnings()` stderr 提示 `--topic-id`

## 验收

- `pytest tests/test_topic_notifications.py` — 4 passed
- 全量相关测试通过
