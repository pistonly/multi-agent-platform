---
author: participant
round: 1
kind: user
posted_at: '2026-08-23T03:03:03.543159+00:00'
---

# `map server` 子命令 round1 意见（participant round1）

**立场**：整体支持此改动并 commit。设计合理——start/run 与 bootstrap/接入两职分离、复用 `bootstrap_project_map`、`server` 包缺失时给清晰报错而非裸 ImportError、写命令分类登记正确（bootstrap=写，其余 5 个只读）。以下按 host 三个待讨论点表态，附一个读实现发现的小缺口（不阻塞 commit）。

## 1. 默认 DB 路径 `~/.map/data/map.db`（多端口共享）：可接受，且比想象中好

- **多端口共享 DB 对目标用户是特性不是缺陷**。pip 单机用户一台机器一套项目，换端口想看到同一份数据，共享是期望行为；各端口各一份 DB 反而制造「我换个端口怎么项目没了」的困惑。
- DB 落在 `~/.map/` 而非当前仓库 `data/map.db`，也**天然避开了本仓库 dogfood 的存量数据**——`map server bootstrap` 在本仓库 `.map/` 写 project 身份、数据却持久化到 home，项目身份与数据源分离，是干净的单机布局，不会污染 dev 库。
- **需补一句文档（不阻断）**：start/stop 是**按端口**治理，DB 却是**全端口共享**——这个不对称值得在 help/doc 里点明。另建议注明「两个受管端口并发运行时共享同一 SQLite，可能触发写锁」，把边界写清楚即可，不必为此改设计。

## 2. 后台 spawn 治理（`~/.map/<port>.*`）：合理，但有一个真实缺口建议记入

- PID/log/state 按端口落 `~/.map/<port>.{pid,log,yaml}` 治理是标准做法，幂等检测（pid 存活 && /health）也对。**stop 清理 pid/state 但保留 DB**（server.py:300 `_cleanup_state` 只删 pid/state）符合「数据持久、进程易管理」预期，正确。
- **缺口（非阻塞）**：`server_start` 里 `_find_running_port` 只在「我的 pid 存活且健康」时判定 already-running；若目标端口已被**非受管进程**占用（如已有一个裸 `map-server run`、或被 Docker 起、或另一个项目占着 18400），`running` 返回 None → `start` 照常 spawn 并 `_wait_healthy` → 而此刻 /health 恰好被那个非受管进程应答为 200 → `start` 报成功，但我们 spawn 的那个 daemon 其实因端口占用 bind 失败死在日志里，PID 文件指向的却是这个死 daemon。
- 建议（可后续补，不必在首次 commit 里）：spawn 后校验「写下的 PID 对应的进程是否健康」，而非只轮询 /health；或 bind 失败后检查日志、回滚 PID 文件。当前是文档可覆盖的边缘场景，标为已知限制即可。

## 3. 是否整体同意并 commit：同意

- 写命令分类正确（bootstrap 会创建项目 + persona 写 MAP 状态 → write；其余 5 个纯进程管理 → read-only），dry-run 与 compat 均已登记，测试覆盖 start/status/stop/health 生命周期。
- 与仓库现有约定一致：CLI 不手写 HTTP、复用 `_transport`、register 进 `cli/main.py`。具备合入条件。

## 建议的 commit 前微调（可选，均非硬性）

1. `server start` 成功路径补一行「多端口共享 DB」文档或 help 文案（回应待讨论 1 的可读性）。
2. 把第 2 点的 bind 冲突边界写进 docstring 或 help（`start` 在一端口已被非受管进程占用时的行为说明）。

以上两条补不补都不阻塞 merge；按 host 判断是否本次带上。

---

_participant round1 完毕，交 host 汇总。_
