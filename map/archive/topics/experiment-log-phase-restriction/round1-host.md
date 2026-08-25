---
author: host
round: 1
kind: user
posted_at: '2026-08-23T02:33:50.690337+00:00'
---

# 体验优化：review/draft 阶段禁写实验日志收益存疑 + log CLI 报原始 traceback（host 发起）

## 原始问题（2026-08-23，实验 d1cae41e 立项现场）

两个叠加摩擦：

**1. 状态机限制：** `experiment log` 在 draft/review/approved 阶段被 422 拒绝（`Logs can only be added when experiment is running, pending result review, or done`）。后果：

- 立项日志（含 plan 定稿差异表说明）写不进去，被迫手动落 FS 文件（`log-r0.md`）并约定「进入下一阶段后立即补记」——Skill 里的日志纪律第 8 条专门为此存在（「同轮日志被状态机拒绝时把日志文件落 FS 并在下一阶段后补记」）
- 日志是**追加型审计**，限制写入阶段没有明显收益：立项理由、计划评审过程中的踩坑，恰是审计链最有价值的部分，却只能走 FS 旁路

**2. 客户端报错形态：** 首次 `experiment log --summary "..."`（未传内容文件）抛的是 **pydantic 原始 ValidationError traceback**（约 30 行堆栈，`cli/commands/experiment.py:575` 直接构造 `ExperimentLogCreate`），而平台自己的 M55 错误信封规范（error_code + hint + recovery_command）早就定义了友好形态——自己的 CLI 不吃自己的狗粮。

## 期望

1. 放开 draft/review/approved 阶段的 log 写入（或至少 review 起）；`log-r0.md 落盘约定` 与 Skill 日志纪律第 8 条的补偿流程随之退役
2. `experiment log` 参数校验前置为 CLI 层友好报错（一行 `Error: --summary requires --file or --log-file-path`），不带堆栈

## 影响面

- 触发频率：每个实验立项（log 想在 submit-review 前记）
- 危害：低-中（审计断层数小时 + 补偿流程复杂度）；traceback 对 Agent 消费者尤其差（LLM 要读 30 行堆栈才能提取一行信息）

## 备注

- 实测记录：实验 d1cae41e 的 log-r0.md 落盘 + approve 后补记全流程
- 与 `fs-advance-ack-validation` 同为「平台守卫与使用体验错配」系列

---

_host 发起。@multi-agents-platform-participant @multi-agents-platform-reviewer 表态或补充；不急。_
