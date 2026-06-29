# P0 实施执行日志

## 完成项

### Part A — Web `pending_topic_replies`
- `web/src/api/types.ts`：新增 `PendingTopicReplyTodo`，`TodoRead.pending_topic_replies`
- `web/src/pages/TodosPage.tsx`：「话题待回复」分区，链接 `/topics/{topic_id}`；empty 判断已包含

### Part B — CLI bootstrap 提示
- `sdk/python/map_client/project_config.py`：`missing_map_config_message()` 含 bootstrap 示例
- `cli/main.py`：`_require_map_dir()` 供 `persona list` 等复用
- `tests/test_cli.py`：`test_cli_persona_list_missing_map_dir`

### Part C — 文档
- `README.md`：「连接已有 MAP 服务」bootstrap 步骤

## 验收
- `pytest tests/test_cli.py::test_cli_persona_list_missing_map_dir tests/test_todos.py` — 6 passed
- `npm run build`（web）— 通过

## 备注
- 议题1（MCP UUID）已在先前 commit 关闭，本实验未涉及
- P1（通知定向、topic_id 软校验）留待 v0.5.1
