---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-09-04T01:01:15.458568+00:00'
---

## Round 1 Summary

### 已共识

host 逐条核对 participant 的 round1 发言，5 个开放问题 + 工作方式协商诉求全部采纳，无分歧：

1. **产品落点**：独立会话桥接组件（Claude Code Stop hook 脚本），**不做成** simple-waker 的 runtime 通道。pull（会话侧 Stop hook）与 push（守护进程 waker）触发源/生命周期/部署位置不同，解耦后独立演进排障；对被唤醒方「通道解耦、协议统一」（wake.md → Skill → `map --persona` 不变）。
2. **去重状态归属**：桥接脚本本地 state file（`.map/interactive-bridge-state-<persona>.json`），字段命名/语义沿用 waker 既有约定（fingerprint / min-remind-seconds），**实例分离**避免与 waker 并发写竞争；脚本保持极简自包含（shell + 标准库）。
3. **空闲盲区与互斥**：本期接受盲区（桥接只覆盖交互在场期，无人值守仍靠 waker 独立 runtime）；互斥用**软信号**（state 写 `last_seen_at` 心跳，waker remind 前检查并对活跃 persona 降级），不做强锁——宁重复、不遗漏；反向重复由 fingerprint 含 work hash 天然消化。
4. **runtime 范围**：本期只做 Claude Code Stop hook，cursor 显式出范围；设计预留 injector 抽象与 state 的 `runtime` 字段。
5. **重复提醒升级**：借鉴 action_item WAKE/STALE 语义，简化为「有限升级 + 提前沉默」——同一 fingerprint 重复提醒升级语气、上限 ≤3 后沉默转 digest；**重复提醒不 block 回合**（block 仅首次），交互场景打扰成本高。
6. **工作方式启动协商（用户诉求，participant 强烈支持）**：Skill 明文规定「交互会话启动时先询问/确认用户希望的工作方式（A waker 全自动 / B 三开对话框 / C host invoke / D 子代理），再按确定方式开启」。落点：`map-project-collab` SKILL.md「快速入口」前加协商小节，四种方式的触发源/读什么 Skill/收尾义务表格化；D 模式特别注明「子代理回报即结束，不做持续跟进轮询」。桥接落地后方式 A 可演化为「A′：waker 全自动 + 提醒投递进交互会话」。

### 未决（带入实验计划）

- 无争议项。实现细节带入实验：injector 接口形态、state 字段终态、fingerprint 具体算法、Stop hook 脚本安装方式（`map skill install` 风格 vs 手动）。

### 下轮议程

- 无 Round 2——Round 1 已收敛，host 标记 `ready` 并开实验。

## 主持状态

- 开实验：**是**。四门 Rubric 全过：≥1 轮讨论 + 本 Summary；`pending_topic_replies` 空；无未闭合争议；participant 本轮已发言（round1-participant.md）。
- 实验范围预告：W1 会话桥接组件 MVP（Stop hook 脚本 + 本地 state + 软信号互斥）+ W2 Skill 工作方式启动协商小节。mode=standard（计划与结果均由 reviewer 门禁）。
