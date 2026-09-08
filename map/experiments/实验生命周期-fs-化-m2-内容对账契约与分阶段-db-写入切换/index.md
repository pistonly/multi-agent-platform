---
title: 实验生命周期 FS 化 M2：内容对账契约与分阶段 DB 写入切换
phase: done
current_plan_version: 1
creator: host
executor: reviewer
topic: experiment-fs-m2-design
updated_at: '2026-09-05T07:20:30.547121+00:00'
description: 落地话题 experiment-fs-m2-design 收敛的 M2 方案：逐字段权威矩阵与规范化 hash 对账、分阶段停止 DB 重复内容写入（flag
  + kill switch + fail closed）、单 publisher CAS 同步契约、存量迁移 manifest 与中断恢复；两轮讨论收敛，实现风险带入实验验证
projection_id: 1b7935e0-5c0f-426b-8237-6ea5e85fe51f
created_at: '2026-09-05T02:15:40.164729+00:00'
---

# 实验生命周期 FS 化 M2：内容对账契约与分阶段 DB 写入切换

落地话题 experiment-fs-m2-design 收敛的 M2 方案：逐字段权威矩阵与规范化 hash 对账、分阶段停止 DB 重复内容写入（flag + kill switch + fail closed）、单 publisher CAS 同步契约、存量迁移 manifest 与中断恢复；两轮讨论收敛，实现风险带入实验验证
