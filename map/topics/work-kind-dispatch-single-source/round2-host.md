---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：方向 B（server KINDS registry 单一真相 → 渲染 + CI 校验）为主，A 作过渡；保留「下一步 Skill」列

## 定稿

1. **方向 B 为最终态（采纳 participant 立场）**：server 维护一份 `KINDS` registry（kind 名 + 清理动作 + **归属 Skill**）→ 渲染 wake.md 的 kind→清理分发表 → CI 校验「分发表行 == registry 渲染结果」。漂移从运行时静默失败变成编译期/CI 期错误。
2. **registry 与产 kind 代码同源**（participant 边界，采纳）：registry 定义放 server 侧、与产 kind 的代码同一处，CLI/md 全部消费之——**单真相定义一次，杜绝二次手写**；避免在 cli/ 另放一份造成次生人工同步。
3. **方向 A 作为 B 的低成本过渡**：`map work --explain <kind>` / `map work kinds` 运行时输出立即可用，wake.md 对应行先静态引用该命令输出；B 渲染管道就绪后切换，两者不冲突。
4. **保留「下一步 Skill」列**（participant 口径 3，采纳为必需字段）：没有它 agent 知道清理动作却不知道该读哪个 persona Skill，仍然断链。
5. **新 kind 落地 checklist 进 handler 文档且为强制项**（participant 口径 4）：「新增 kind 必须同时改 registry + 渲染 + 测试」，CI 封住「只改 server 不改表」。
6. **不过度机械化**：清理动作里「按内容选」等需 agent 判断的条目，registry 只给「动作类别 + 主观标准」，不假装可枚举（participant 边界 2）。

## 动线

- 开实验落地：server KINDS registry + `map work kinds`/`--explain` 输出 + wake.md 渲染 + CI 一致性校验 + 清单含归属 Skill。
- 验收：新加一个 kind（测试桩）→ 不改 wake.md 时 CI fail，改后 pass；`map work kinds` 输出与 wake.md 分发表逐行一致（脚本 diff）；真实唤醒拿一个 obligation，agent 依表完成清理（端到端冒烟）。
