# experiment 5a50c841 complete — host invoke 编排可观测性 v1(commit 3b2f4df)

## summary
host invoke 编排可观测性 v1 五项验收全部落地:`--timeout`(到点友好报错 + 经新增
`POST /agents/me/notifications/dispatch` 端点向被调 persona 发 wakeable
`host.invoke.cancelled` 取消通知)、`--follow`(流式 text/tool 事件实时打 stderr、
stdout 仅在结束时含最终结果)、启动状态行(A3:session_state waiting-for-session/
running)、cookbook prompt 首段对象引用约定(A4)、cookbook 双 invoke 后台并发 +
平台对象汇合点模式(A5)。执行记录见 `map/experiments/host-invoke-observability/log-r1.md`
(平台 log id 由 `experiment log` 记载,validation.valid=true)。

## 实施 log
- I1 —— orchestrator.py(InvokeResult 加 session_state/timed_out/waited_seconds;
  invoke 加 timeout/follow/on_stream;asyncio.wait_for 包裹 timeout)+ server/api/agents.py
  dispatch 端点 + map_types.NotificationDispatchCreate + SDK dispatch_notification。
- I2 —— on_event 在 follow=True 时经 on_stream 前转;host.py 渲染 text/tool_use/
  tool_result 到 stderr。
- I3 —— execution-cookbook 参数表补 --timeout/--follow、状态行说明、A4 首段对象
  引用约定、A5 双 invoke 并发+汇合点示例。
- I4 —— trio 实测:+ 单测 24(orchestrator/CLI)+ 集成 3(dispatch 端点)+ mypy(agents)
  + ruff 全改文件通过;notification 系列唯一失败经 stash 验证为既有债务。

## 风险
- timeout 取消是「通知语义」而非强杀:被调方 session 在 host 超时后仍挂在其
  persona 侧,靠收到 host.invoke.cancelled 后自行收尾。v1 接受该取舍(plan
  「取消通知」验收定义 + participant 修正 3);后续如需硬终止可再加 session-kill。
- dispatch 端点是 agent→agent 通知通道的**最小补充**,校验仅限同 project + 非 self;
  无审计/限流告警,滥用面小(仅登录 agent 可用),后续话题可按需加策略。
- host invoke 实跑依赖 claude_agent_sdk,运行入口是 miniconda base `map`(仓库
  源码可编辑安装),非 .venv(仅 server daemon 用);文档已在 log-r1 备查段记录。

## acceptance(A1-A5 对照)
| 验收 | 状态 | 证据 |
|------|------|------|
| A1 --timeout 生效+友好报错+被调方收到取消通知 | ✅ | 实测 stderr 报错(含 1s + state=running,无堆栈,exit1);participant 收到 host.invoke.cancelled(wakeable/id ed820d8d);dispatch 端点集成测试 3 项 |
| A2 --follow 流式(stderr 实时/stdout 仅最终) | ✅ | 实测截录:stdout=probe ok,stderr=流式+[orchestrator] 行 |
| A3 启动状态行(waiting-for-session/running) | ✅ | 实测两次 invoke state=running;单测覆盖 running/waiting 两态 |
| A4 prompt 首段对象引用约定 | ✅ | execution-cookbook 「Prompt 首段对象引用约定(A4)」段落 grep 核证 |
| A5 cookbook 双 invoke 后台并发+汇合点模式 | ✅ | execution-cookbook 「双 invoke 后台并发 + 汇合点模式(A5)」grep 核证;汇合点走平台对象 |
| 测试面 | ✅ | orchestrator 24 + notifications 8(含 dispatch 3 新增)+ agents mypy + ruff 全改文件绿 |
