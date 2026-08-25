---
title: 合并 github/main 到本地 main：分叉 19 vs 28 提交，如何对齐
status: closed
round: round1
creator: host
created_at: '2026-08-23T16:07:19.577145+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 采纳 participant Round 1 五条立场（实证核证到位，host 全部拍板）：

  1) 合并方式 = 直 merge 保留双亲历史（非 rebase）——map/ 协作资产大量引用 commit hash（4b1192cc/207d7c4b/a024bbf
  等），rebase 重写 SHA 会断审计链；合并后 push origin（本地 ahead origin，非 force）

  2) fast-gate-allowlist-inversion 两侧经 git diff 实证逐字节一致 = 同一实验镜像，无优先/重复冲突，merge 自动无冲突

  3) 在途修改先提交再 merge（缩小冲突面）：fs stale-nudge 修复（fs_source_service.py/tests/test_fs_source.py/wake.md/perf
  baselines）与未跟踪 map/ 资产先梳理齐整；advance-ack 部分已随 4e45f4e 提交

  4) 未来同步 = github 作跨源发布基线（PyPI/CI），origin 内网主产地；每次 merge/publish 前后把两边拉齐，单向 push
  github 对齐（不做持续自动同步）

  5) 不开实验：纯 git 操作可回退、验收 = git log/diff/pytest 可证伪

  action_items:

  - host 执行 merge：merge-base 已复核 cef5ed3；git merge-tree 冲突面与 participant 清单一致 = 9
  文件（4 代码 cli/main.py + sdk/python/map_client/client.py + sdk/python/map_types/schemas/__init__.py
  + server/api/agents.py；5×topic index.md 两侧取最新；alembic/docs/fast-gate/web 零冲突复核一致）。验收
  pytest 子集 + git log --first-parent；完成后 push origin + push github/main 对齐 —— push
  github 为对外动作，执行前需用户显式确认'
---

# 合并 github/main 到本地 main：分叉 19 vs 28 提交，如何对齐
