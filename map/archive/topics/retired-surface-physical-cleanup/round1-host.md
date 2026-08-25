---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# 退役面只退文档不退文件:host bridge 脚本仍在,退役命令靠「勿依赖」提醒(host 发起)

## 原始问题

CLAUDE.md/skill 多处声明「cli/host_worker bridge 与 legacy runtime-waker 启动路径已停用」,但:
- scripts/start-host-bridge*.sh、start-participant-bridge*.sh、start-reviewer-bridge*.sh、start-all-wakers.sh(legacy 组)仍躺在仓库
- DB 写命令退役(v0.13 M58)靠返回引导性错误 + 文档「勿依赖」——文档纪律对抗不了文件存在的事实,新 agent/人仍可能误启动

## 期望(讨论口径)

- 退役脚本物理删除(git 历史可找回)或 stub 化(echo 指引 + exit 1)
- 退役清单机器化:CI/lint 规则(grep 已声明退役的入口仍存在 → fail)
- docs/MAP-SIMPLE-WAKER.md 与 scripts/ 目录一致性核对

## 证据坐标

- 声明处:CLAUDE.md(Waker 与职责边界)、.claude/skills/experiment-host(「已停用(勿依赖)」段)
- 残留:ls scripts/(bridge/legacy 组)
- 先例:DB 写路径退役做了 stub(_db_write_retired 引导性错误),脚本侧未跟进
