# experiment 5a50c841 — host invoke 编排可观测性 v1:running → 执行收尾(r1)

> 2026-08-23。host invoke 编排可观测性：--timeout 取消通知 / --follow 流式 /
> session 状态显式化 / Skill 并行编排示例。验收 A1-A5 全部落地,commit 3b2f4df。

## 实施概览(I1-I4)

### I1 状态行 + --timeout 取消通知(A3+A1)
- `cli/orchestrator.py`：`HostOrchestrator.invoke` 增加 `timeout` / `follow` / `on_stream`
  参数;`InvokeResult` 新增 `session_state`(A3:waiting-for-session/running)、
  `timed_out`、`waited_seconds`。timeout 用 `asyncio.wait_for` 包裹 wake_up,
  到点返回 status="timeout" 的友好错误(含已等待时长 + 目标 session 状态,**无堆栈**)。
- 取消通知通道(A1)：新增最小 server 端点 `POST /agents/me/notifications/dispatch`
  (`server/api/agents.py` + `map_types.NotificationDispatchCreate`),通过既有
  `notification_service.enqueue_for_agents` 复用通知管道,wakeable 显式可传;
  SDK 加 `map_client.client.dispatch_notification`。CLI 的 `host invoke --timeout`
  到点打印友好报错并向被调 persona 发一条 wakeable `host.invoke.cancelled`
  通知——**语义是「通知对方会话已被放弃」而非静默杀进程**(会话仍挂在其
  persona 侧,由被调方自行收尾;plan 风险段 + participant 修正 3)。

### I2 --follow 流式透传(A2)
- `HostOrchestrator` 的 `on_event` 在 follow=True 时把 text/tool_use/tool_result
  事件实时转发给 CLI;`host invoke --follow` 打到 **stderr**,stdout 仍只在
  结束时含最终结果(stdout=数据 / stderr=人类可读)。

### I3 Skill 文档(A4+A5)
- `.cursor/skills/experiment-host/references/execution-cookbook.md`：
  - A4：prompt **首段固定对象引用**(topic slug / experiment-id / skill 名)约定;
  - A5：「双 invoke 后台并发 + 汇合点」模式——两个 invoke 后台并行 + **汇合点
    查询走平台对象**(`map work` / `topic show` / `experiment status`),`--follow`
    stderr 日志仅用于诊断「卡在哪一步」,不作完成判定。
  - 参数表补 `--timeout` / `--follow` 与启动状态行说明。

### I4 验证(测试面)
- 单测：`tests/test_orchestrator.py` 新增 8 项(A3 两种状态、A1 timeout 元数据与
  友好报错、A2 follow 前转/不转、CLI --timeout/--follow 传透、timeout exit 1 +
  dispatch、dispatch helper wakeable 语义),共 24 passed;
  `tests/test_notifications.py` 新增 dispatch 端点集成测试 3 项(agent→agent
  wakeable、self-dispatch 400、未知 recipient 404),共 8 passed。
- `server/api/agents.py` 过 `test_eng_mypy_strict_agents`;notification 系列
  (bulk_filter / service_v09 / upsert_pr2 / topic / lock / action_item)28
  passed,唯一失败 `test_stalled_lock_scan_endpoint_host_scopes_to_own_project`
  经 git stash 验证为**既有债务**(无本实验改动时同样失败,与 dispatch 无关)。
- `ruff check` 全改文件 through;全库仅剩 3 处既有无关错误
  (session_wake_log UP038、两个测试 I001 import 排序)。

## 实测证据(A1/A2/A3)

1. **--follow 截录**(participant 真实会话,秒回 probe):
   - stdout: `probe ok (participant)`(仅最终结果)
   - stderr: `probe ok (participant)`(流式 text)+
     `[orchestrator] persona=participant status=ok session=0f84cbee... state=running`
2. **启动状态行(A3)**: 同会话第二次 invoke,结束行 `state=running`(复用既有
   session);首次为 waiting-for-session(单测覆盖)。新建/复用两类均已核证。
3. **timeout 报错 + 取消通知(A1 全链路)**:
   - `host invoke --timeout 1` → stderr:
     `Error: waiting for 'participant' exceeded 1s and was cancelled; target session state=running`
     (无堆栈,exit 1)
   - `[orchestrator] 已向 multi-agents-platform-participant 发送取消通知`
   - participant 侧 `map notification list`：新增
     `host.invoke.cancelled`(wakeable / unread,summary 含已等待时长 + session 状态)。
4. **dispatch 端点 404 → 修复**:首测通知发送失败(旧 server 无新端点),重启本地
   server daemon(保持规范库 `claude_home_zai/.map/data/map.db`、`.venv` python)
  后重测通过——验证了「改 server 源码必须重启 daemon 而非只用旧进程」。

## 备查

- `commit_sha`: `3b2f4df`
- `git_status_after`: 本实验 9 文件改动全部提交;工作区剩余脏文件均为其他话题/
  实验的既有进展(5 个 topic index、round2-host/participant、perf-baselines、其他
  实验目录),与本实验无关。
- 环境说明：`host invoke` 需要 `claude_agent_sdk`,运行入口是 PATH 里的
  miniconda base `map`(仓库源码可编辑安装),非 `.venv`(仅 server/daemon 用)。
