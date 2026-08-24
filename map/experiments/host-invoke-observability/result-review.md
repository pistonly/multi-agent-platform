# host invoke 编排可观测性 v1 — 结果审批（accept）

## 结论

**通过**。A1–A5 与测试面逐条满足，commit 3b2f4df（+88723b9 实验目录入库）收口完整，关键路径有实测 + 可复跑单测双重证据。

## 验收逐条核验

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 --timeout 生效 + 友好报错 + 被调方收到取消通知 | ✅ | `cli/orchestrator.py` HEAD：`asyncio.wait_for` 包裹 wake_up，`InvokeResult(status="timeout"/timed_out/waited_seconds)`，报错含时长 + session 状态、无堆栈；新增 `POST /agents/me/notifications/dispatch` 端点（`server/api/agents.py`）+ SDK `dispatch_notification`，发 wakeable `host.invoke.cancelled` 通知。实测：`--timeout 1` → stderr 报错 + participant 收到通知（id ed820d8d） |
| A2 --follow 流式 | ✅ | `on_stream` 透传 text/tool_use/tool_result 到 CLI host.py 渲染 **stderr**；stdout 仅在结束时含最终结果。实测截录：stdout=probe ok / stderr=流式 + `[orchestrator]` 状态行 |
| A3 启动状态行 | ✅ | `session_state = waiting-for-session / running`（复用 vs 新建会话二值语义显式化）；实测两次 invoke `state=running`，单测覆盖两态 |
| A4 prompt 首段对象引用约定 | ✅ | execution-cookbook「Prompt 首段对象引用约定（A4）」段落（HEAD grep 核证） |
| A5 cookbook 双 invoke 后台并发 + 汇合点模式 | ✅ | execution-cookbook「双 invoke 后台并发 + 汇合点模式（A5）」：汇合点查询走平台对象（`map work` / `topic show` / `experiment status`），`--follow` 仅作诊断；示例可直接复制执行 |
| 测试面 | ✅ | `tests/test_orchestrator.py` **实测 24 passed**；`tests/test_notifications.py` **实测 8 passed**（含 dispatch 3 项集成：agent→agent wakeable / self-dispatch 400 / 未知 recipient 404）；agents mypy + ruff 全改文件绿 |

## 关键核验点

1. **commit 收口完整**：`3b2f4df` 为 HEAD 祖先，改动 9 文件（orchestrator.py +76 / host.py +114 / agents.py +56 / SDK client +33 + schema / cookbook +44 / 两个测试文件）与实验声明一一对应；`88723b9` 将实验目录（plan/review/acceptance/log-r1）入库。
2. **单测真实复跑全绿**：orchestrator 24 passed（0.41s）+ notifications 8 passed（26.19s 集成），非仅信 log 声称。
3. **dispatch 端点安全校验完整**：404 未知 recipient → 403 跨 project → 400 self-dispatch（`enqueue_for_agents` 天然排除 actor）；`wakeable` 显式可选，通道复用既有 notification 管道，无 schema 破坏性改动。
4. **A1 语义与计划/participant 修正一致**：超时 = 「通知取消」而非强杀进程，被调方 session 留其 persona 侧自行收尾——与 plan 风险段、participant 修正 3 完全一致。
5. **environement 备注可信**：`host invoke` 实跑依赖 PATH 中 miniconda base `map`（可编辑安装源码）、非 `.venv`（仅 server daemon 用），已在文档备查记录——审批不依赖重跑该环境。

## 遗留（非阻塞）

- `test_stalled_lock_scan_endpoint_host_scopes_to_own_project` 失败经 git stash 验证为既有债务（与本实验改动无关），不属本实验验收面。
- dispatch 端点无审计/限流策略（仅登录 agent 可用、同 project 校验），log 已明示滥用面小，可留后续话题按需补充。
