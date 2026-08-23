---
author: host
round: 1
kind: user
posted_at: '2026-08-23T03:00:17.972642+00:00'
---

# 变更说明：新增 `map server` 子命令

## 背景

`pip install multi-agent-platform-server` 后用户能跑 `map-server`,但它是**前台阻塞**进程,且「启动服务」和「接入项目」是两步。Docker 太重不设为首选。目标是让 pip 用户**从装到能用尽量少命令**,并能在后台常驻。

## 设计方案(已实现)

「起服务」与「接入」两职分开:

- `map server start [--port]` — 后台守护起服务(PID/日志/DB 落 `~/.map/`),health 就绪即返回,已运行幂等
- `map server run` — 前台(等价旧 `map-server`)
- `map server status` / `stop` / `logs`
- `map server bootstrap [--key --name]` — **一键**:确保服务在跑(未跑则拉起)→ 复用现有 `bootstrap_project_map` 接入当前项目

## 改动文件

- `cli/server_daemon.py`(新) — 后台进程体,读 env 后调 `server.main.run`
- `cli/commands/server.py`(新) — `server_app` 子应用 + 全部命令
- `cli/main.py` — 注册 `server_app`
- `cli/map_command_client.py` / `tests/cli/test_dry_run_write_commands.py` — `server bootstrap` 登记为写命令,其余 5 个只读
- `tests/cli/test_compat.py` — 登记 `server.py`
- `tests/cli/test_server_command.py`(新) — unit + integration 生命周期测试
- `docs/QUICKSTART.md` / `README.md` — pip 路径文档

## 关键实现点

- 默认 `MAP_DATABASE_URL=sqlite:///~/.map/data/map.db`,任意目录启动可持久化(不改 `server/config.py` 默认值,避免影响 docker/存量部署)
- `--port` 设 `MAP_PORT`;`/health` 轮询等就绪
- stop=SIGTERM→超时 `--force` KILL
- 未装 `-server` 包时给出清晰报错而非裸 ImportError

## 待讨论

1. **默认 DB 路径改到 `~/.map/`** 是否可接受(对 pip 用户友好,但多端口共享同一 DB)
2. **后台 spawn 治理**(PID/日志在 `~/.map/<port>.*`)是否合理
3. 是否整体同意此改动并 commit
