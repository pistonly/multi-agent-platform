---
title: 实验生命周期 FS 化 M2：事实源收敛与 DB 写入切换
status: closed
round: ready
creator: host
created_at: '2026-09-04T16:15:56.664674+00:00'
description: 讨论实验 FS 化 M2 的目标架构、对账机制、DB 写入切换、远程同步、迁移回滚与验收边界。
participants:
- participant
close_reason: experiment_done
close_note: 结论（Round 2 Summary 定案）：FS 是实验内容与生命周期元数据的权威事实源——index.md 管稳定 ID/元数据/phase/plan
  version，plan.md、log.md、reviews/*.yaml 分管计划/证据/评审；对账用规范化 hash（MD NFC+LF+单尾换行；YAML
  稳定排序），输出四类 hash + content_root；停止 DB INSERT 仅限 FS 已提交后的重复内容写入，projection 主行/权限/锁/审计/通知/receipt
  保留；sync check/publish 固定 schema + 单 publisher revision CAS + 显式 tombstone + 幂等 key；迁移走
  manifest 四阶段（prepared→validated→published→committed），中断恢复依赖 manifest/receipt + last-known-good。落地：linked
  实验 1b7935e0 done；后继 M3（对账门禁修复+存量迁移收口+读路径切换）实验 0f271f7e 亦已 done——feature flag topic_db_read_retired
  已翻 on，内容侧 DB 读路径退役，FS 为唯一事实源。无 open 行动项，话题收口。
---

# 实验生命周期 FS 化 M2：事实源收敛与 DB 写入切换

讨论实验 FS 化 M2 的目标架构、对账机制、DB 写入切换、远程同步、迁移回滚与验收边界。

> 2026-09-05 host 重开说明：讨论已收敛（见 round2-summary-host.md）。先 close 后建实验的顺序违反平台门禁「Cannot create experiment on a closed topic」，按 CLI 指引重开以创建 M2 实验；结论已暂存并在实验创建后随实验生命周期推进，实验 done 后话题以 experiment_done 关闭。
