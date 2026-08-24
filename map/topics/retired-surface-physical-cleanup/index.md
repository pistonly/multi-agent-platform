---
title: 退役面只退文档不退文件:host bridge 脚本仍在,退役命令靠「勿依赖」提醒
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:37.344745+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 退役启动脚本 stub 化优先不默认物理删除——legacy 组(start-*-bridge*.sh / start-all-wakers.sh)保留文件名改
  echo 指引 + exit 1(stderr 点明正确替代命令 start-simple-waker.sh),两阶段(先 stub 观察一周期再评估删);CI/lint
  兜底防回潮(已声明退役仍可执行非 stub → fail;新增未登记启动路径 → fail);docs/SIMPLE-WAKER.md 与 scripts/ 逐文件对照;rationale:
  participant 两轮表态无异议,附补充(物理删除阶段走显式 diff 评审,至少 host 之外一人看过删除清单);action_items: [] 收敛时无持有执行项,落地走待开实验'
---

# 退役面只退文档不退文件:host bridge 脚本仍在,退役命令靠「勿依赖」提醒
