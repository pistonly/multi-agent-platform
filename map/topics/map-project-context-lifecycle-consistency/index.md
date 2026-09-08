---
title: 统一 MAP 项目根目录与生命周期提交一致性
status: closed
round: ready
creator: host
created_at: '2026-09-02T01:11:20.826922+00:00'
description: 解决 --project-root、CWD 与 map/** 写回分叉，并统一实验生命周期的可恢复提交协议。
participants:
- participant
close_reason: experiment_done
close_note: 结论：议题双线收敛。①I1 已由实验 A（e7244a91）完成并验收 @852fc5c——A1 ProjectContext 单点解析（16
  处 find_map_dir(None) 调用点收口至 cli/project_context.py）、A2 双根隔离 subprocess 证明（CWD=A
  只写 B）、A3 --config-root 三级优先级 fail closed、A4 st_dev+st_ino 根指纹（落实 R2-D3 非路径字符串约束，规避
  macOS 双挂载同 inode 误报）+ sync check 读路径 warn、A5 AST 静态守卫清零、A6 pytest 2072 passed/0
  failed、A7 边界未越；R2-D1~D4 四项边界 participant 全部接受。②遗留去向：I2+I3（validate→commit CAS、receipt/token
  七元组+TTL+GC+recover、direct complete 不开旁路、recover 冲突报告输出双方证据摘要、指纹 lifecycle fail closed
  门禁）归实验 B，B 计划须基于 A 落地后的 context 传递接口编写。③执行反馈记录：tests/test_red_line_clause.py 实际不存在，但
  CLAUDE.md/wake.md/SKILL 多处引用其作副本漂移检测（e6d23886 I9 悬空引用）；map/experiments 下 hygiene-a4/、slim/
  两投影目录 untracked——均留待后续 hygiene 话题处理，不阻塞本话题关闭。
---

# 统一 MAP 项目根目录与生命周期提交一致性

解决 --project-root、CWD 与 map/** 写回分叉，并统一实验生命周期的可恢复提交协议。
