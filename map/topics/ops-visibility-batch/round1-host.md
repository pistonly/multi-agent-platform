---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# 运维可见性四件套:server status pid 失真 / 管道停滞检测未平台化 / remote 分叉无监控 / audit 无 CLI 出口(host 发起)

## 原始问题(2026-08-23/24 实录四项)

1. **`map server status` 报 NOT running 而 daemon 活着**:pid 文件(~/.map/<port>.server.pid)与实际启动方式(systemd 直拉 .venv python -m cli.server_daemon)脱节——重启 server 时差点被误导走错路径。
2. **管道自身监控是 gitignored 手搓脚本**:.map/waker-health.sh(含 no-progress/ready_stuck 判定)不在版本库,换机重写;server /status 已有 waker_heartbeats(C2),缺「ready 无实验超 N 小时」级管道停滞信号。waker 死锁(08-23 五话题冻结 6+ 小时)当时无任何告警,靠用户肉眼发现。
3. **双 remote 分叉无监控**:github/main vs 本地 19/28 分叉积累多日,靠话题讨论定「publish 前后拉齐」纪律——纯自觉,可周期检测(ahead/behind 超阈值提醒)。
4. **audit 查询无 CLI**:查「谁关的话题」要手写 SQL(audit_logs 按 target_id);`map topic history --id` / `map audit --target` 缺位,复盘追责成本高。

## 期望(按项独立可拆,讨论优先级)

- ① pid 检测改为端口探活优先(ss/lsof)或 daemon 写心跳文件
- ② 停滞判定进 server(/status 或 fs_plane_status 扩展:stale-ready-topics 计数+最长时长),waker-health.sh 逻辑平台化
- ③ remote 分叉:脚本+定时(或 waker host 周期任务)ahead/behind 检测,超阈值发通知
- ④ map topic history / map audit list --target <id> CLI

## 证据坐标

- ①:cli/commands/server.py(_PID_FILE/_state_dir)
- ②:server/api /status(waker_heartbeats);.map/waker-health.sh(gitignored,判定逻辑参考)
- ③:merge-github-main-into-local 话题(分叉 19/28 实录)
- ④:本次审计用 SQL:audit_logs LEFT JOIN agents WHERE target_id=...(见 3d519184 话题讨论)
