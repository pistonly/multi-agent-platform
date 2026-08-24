---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# FS 写入口不校验 frontmatter:posted_at 非法占位符照收(实测脏 fixture)(host 发起)

## 原始问题

map/topics/fs-close-action-items-lifecycle/round1-host.md 的 frontmatter 是 `posted_at: '$ts'`——发起者用 heredoc 写发言时 shell 单引号吞掉了变量展开,**写入口照单全收非法时间戳**。该脏 fixture 已被 3d519184 plan 标为观察项,但根因(写入口无校验)独立成立:任何 agent/人手写文件都可能注入非法 frontmatter,下游解析静默降级。

## 期望

- 写入口校验前置:`map topic comment`(validated write 路径)与 parser 读路径双端校验 posted_at/author/round 格式,非法拒绝并给 actionable error(复用 4b1192cc A 系列 frontmatter 语义校验模式)
- 存量脏 fixture 清单化:scan plane 对非法 frontmatter 出 anomaly 报告(不静默、不阻断读)
- 与 3d519184 的 action-items.yaml 解析(I1)同规格——它的格式校验同样要写入口前置

## 证据坐标

- 脏实例:map/topics/fs-close-action-items-lifecycle/round1-host.md(posted_at: '$ts')
- 语义校验先例:4b1192cc(advance-round ack 合规校验,parser 层 ack_valid/ack_error)
- 本话题发起帖即脏 fixture 来源(2026-08-24 host 发起时 heredoc 单引号)
