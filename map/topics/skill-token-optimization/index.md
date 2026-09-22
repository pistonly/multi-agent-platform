---
title: Skill 分发面 token 开销审计：唤醒链路瘦身
status: closed
round: ready
creator: host
created_at: '2026-09-21T11:10:21.377068+00:00'
participants:
- participant
close_note: '收尾结论（实验 4e4206de 结果审批通过后关闭）：


  **决策**：唤醒链路 token 优化采纳 participant 核心修正（静态文件大小 ≠ 运行时实际加载 token），按三阶段推进；本话题承载 Round
  1 收敛 + 阶段一/二实验，阶段三另起。


  **已完成（实验 4e4206de，done，reviewer 结果审批通过，A1–A8 逐条核对，全量 2754 passed / ruff 干净，CLI --json
  机器契约不变）**：

  - 阶段一（安全速赢）：#7 删 whoami 与 work agent 块冗余（三处一致化，work-first）；#9 run-map.sh 分发模板补清
  PYTHONHOME/PYTHONPATH + QUICKSTART 指引；#6+#10+#11 CLI 默认输出裁剪（work 空分区折叠、experiment
  plan 默认只给版本数+文件指针、全文走 --full），保留 --verbose/--full/--status all 逃生口。

  - 阶段二（测量面）：CLI 出口记账 JSONL（persona/kind/读取文件+字节/调用次数/输出字节）+ `map usage summary` 聚合命令。


  **明确不动**：#5 红线分发副本保留（单源 lib/red_line_clause.py + 副本守卫是分发面设计）；topic show / topic
  progress 克制设计保持；--json 契约不动。


  **遗留（不在本话题展开）**：阶段三结构性瘦身（#1 kind→Skill 路由连带 registry 三处同步、#2 三层上下文脊柱、#3 路由评测驱动的
  description 瘦身、#4 legacy-db-topics.md 收编 + stale 路径措辞清理）——待阶段二 JSONL 测量数据回填后，以数据重排细目，另开话题/实验。A4
  SKILL.md --full 指引子项为 reviewer 认定的可忽略后续项。


  action-items.yaml 无执行项（零尾款），实验 done，符合关闭门禁。'
---

# Skill 分发面 token 开销审计：唤醒链路瘦身
