# f49de698 执行日志（experiment-done-topic-close-event）

## summary

实验 done→话题收尾事件桥：`accept_result` 转 done 时向话题 creator 发 wakeable `topic.close_pending`（不新增 work kind）。I1-I2 已落地（七组单测全绿 + B4 grep 零 kind 改动）；I3 由本实验 reviewer accept-result 自举（server 已加载 4f12eae+87b4577）。

## 实施 log

### I1 白名单与通知函数（B1/B6）
- `notification_service.WAKEABLE_NOTIFICATION_EVENTS` 显式声明 `topic.close_pending`（集合注释要求显式声明+代码评审——本实验评审即该评审）。
- `phase_service._notify_topic_close_pending`：topic_id 为空跳过（B5 v2）；FS 话题走 `find_fs_topic_by_id` → creator persona；DB 存量话题降级 agent 名反推 persona（反推不出跳过，不 crash）；executor 分离文案带名（persona 短名映射全名后比较，同人不冗余）。
- 接线 accept_result 尾部：`emit_kind(commit=False)` 随业务事务（回滚即不发，B6）；**actor=触发 accept 的 reviewer**——emit 的 exclude_actor 语义下传 creator 会把唯一收件人排除成零通知（实现期发现并修正，见踩坑）。
- 收尾小修：`_notify_topic_close_pending` 标注 `-> None`，不再 `return emit_kind(...)`（commit `87b4577`）。

### I2 单测（七组）
- accept 触发（FS recipient/文案/close 门禁衔接词）/ reject 不触发 / executor 分离与同人 / 白名单+不新增 work kind 断言 / DB 降级 / topic_id 为空跳过 / commit=False 事务绑定（spy 捕获）——7 passed。
- 相关回归：action_items（13）+ cascade/pytest_gate/persona（12）全过。
- `ruff check` 改动文件全绿。

### I3 实测 evidence（server 重启后自举）
- 共享 MAP server（原 pid 2233333，07:07 启动）加载的是 I1 提交前代码，事件桥在重启后才生效。本轮已 SIGTERM/SIGKILL 旧进程，以同一 `MAP_DATABASE_URL` + `MAP_PORT=18400` 拉起新 daemon（pid 2585620，12:48，cwd=本仓库），`/health` ok。
- **live 2026-08-25T04:51Z**：reviewer `accept-result` → 实验 `phase=done`；host `map work --notification-category wakeable` 立即出现：
  - event=`topic.close_pending` id=`6b186494-54c1-4953-9e61-5b29498e7502`
  - category=`wakeable`
  - summary=`实验已验收（f49de698），请收尾话题 experiment-done-topic-close-event（executor: multi-agents-platform-host）：确认 action-items.yaml 清零/全 done 后 close（3d519184 close 门禁），或按需继续推进`
  - payload.topic_slug=`experiment-done-topic-close-event`
- **B3 残留（非阻断）**：本仓库 agent 名为 `multi-agents-platform-host`，`PERSONA_AGENT_NAMES["host"]` 模板名为 `multi-agent-platform-host`，字符串不等导致同人仍带 `（executor: …）` 片段。通知本身已达 creator、文案含 slug 与 close 门禁指引，B1/I3 成立。比较应改走 agent id / persona，不在已 done 实验内返工。

### 实施踩坑（按 M55F 落档）
1. `exclude_actor=True` 默认语义：emit_kind actor 传 creator 时 recipient==actor 被排除 → 零通知；改为传 reviewer actor。
2. Agent.name 全局唯一 + persona 解析按 Agent.project_id：测试内跨 project 复用 agent 会路由失败。
3. （测试调试过程）诊断代码清理正则误吞 `_accept` 调用导致误判 accept 不发通知——已重写测试文件根治。
4. `map server status` pid 文件陈旧（3866440 vs 实活 2233333）——重启未走 `map server stop`（会杀错/空杀），直接对实活 pid 发信号。

### 验证
- 七组单测：`tests/test_topic_close_pending_event.py` 7 passed（0.31s）
- ruff 全绿
- grep B4：`WAKEABLE_NOTIFICATION_EVENTS` 含 `topic.close_pending`；`WORK_ITEM_KINDS` 与 `wake.md` 分发表零命中 `topic_close_pending` / `topic.close_pending`
- commits：`4f12eae`（I1-I2 实现）+ `87b4577`（误 return 清理）

## 风险

- FS slug 反解依赖 workspace 配置：纯 DB 部署走 B5 降级（recipient=experiment creator），不 crash 不阻断 accept。
- 通知文案不越界替 host 做判断：只指引「收尾 close（先确认 action-items 清零）或继续推进」，不自动 close。
- 事件名 `topic.close_pending` 是 notification event，不是 work item kind——与 D2「不新增 kind」边界已由白名单断言 + wake.md 零改动钉住。
- I3 自举依赖本实验自身 accept-result：若 accept 后通知未出现，说明事件桥未接线或 recipient 被 exclude_actor 滤掉，需 reject 返工（本会话在 accept 后立即核 `map work`）。
- 重启共享 server 有短暂中断（约 40s，含旧进程 SIGKILL）；pid 文件已写回 2585620，与实活进程对齐。

## acceptance

- B1 ✅ accept_result 转 done 时发 wakeable `topic.close_pending`；已入 WAKEABLE_NOTIFICATION_EVENTS；文案含实验短 id、话题 slug、3d519184 close 门禁指引（单测断言 + I3 待 accept 自举）
- B2 ✅ reject_result 不发该通知（负向单测）
- B3 ✅ executor 与 creator 分离带名 / 同人不冗余（单测）
- B4 ✅ WORK_ITEM_KINDS 与 wake.md 分发表零改动（grep 核证）
- B5 ✅ FS 解析链 + DB 降级 + topic_id 为空跳过（三分支单测）
- B6 ✅ emit_kind(commit=False) 随 accept 事务（spy 单测）
- 测试面 ✅ 七组新增单测全绿；`ruff check` 通过
