# Current Status — multi-agents-platform

> 本文件为 **status_md 叙事参考**（v9）。清单类数据以 `get_project_status` 快照字段为准。

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agents-platform` |
| workspace_path | `/home/AI02/Documents/quantaeye/multi_agents_platform` |
| 主分支 | `main` |
| 最新版本 | **v0.8 waker 闭环已验收**（runtime-waker 标准路径） |

## Agent 身份与协作路径

本仓库 **统一使用 `.map/` persona + `map` CLI + runtime-waker**。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agents-platform-host` | 主持话题、创建实验、推进生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划 |

**标准路径**：`./scripts/start-all-wakers.sh` → Agent 读 Skill → `map --persona <name>` CLI。

**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`、runner JSON 契约。

## v0.8 闭环验收（实验 `c9776cb4`，plan v3）

| 子项 | 摘要 |
|------|------|
| I1 | 删除 `ROUND_SUMMARY_RE`，轮次门禁改读 `round_summary_count` |
| I3 | `scripts/systemd/map-wakers.service` + install.sh + 测试 |
| I4 | advance-round ack（24h silence=consent、reject 409、topic-host Skill） |
| SSE A/C | `test_sse_isolation.py` + `test_sse_schema_overlap.py` |
| P3 dogfood | 探针 `90cba1c3` draft→done；三 waker `wake_errors: 0`；未启动 bridge |

C 类保留正则清单见 [MAP-RUNTIME-WAKER.md](../docs/MAP-RUNTIME-WAKER.md#v08-保留正则不在-round_summary_re-清理范围)。

## 已完成功能（里程碑续）

| 阶段 | 内容 |
|------|------|
| M25–M26 | v0.7 P1 轮次字段 + P2 host bridge（**legacy，不再扩展**） |
| M27 | v0.8 runtime-waker 标准路径、systemd 部署、ack 门禁、SSE acceptance |

## 技术栈

- **后端**：FastAPI + SQLAlchemy + Alembic
- **前端**：React + Vite + TypeScript
- **协作**：CLI (`map`)、runtime-waker、`.cursor/skills/`
- **部署**：Docker Compose（API :8001 / Web :3000）；可选 systemd `map-wakers.service`

## 阻塞 / 风险

- SSE 仍为单进程 pub/sub；多 API 副本需外部扇出
- `cli/host_worker` 仍留于仓库作 legacy，计划单独实验删除
- `tests/test_experiment_lock.py` 与 integration 同名模块存在 pytest 收集冲突（`--ignore` 规避）

## 下一步（建议）

1. 删除 legacy host bridge / runner 脚本（单独 issue）
2. v0.9 Codex 探针（触发：v0.8 waker 闭环已完成）
3. 修复 pytest 模块名冲突；SSE B 模块闭包检查
4. CI 持续：pytest + vitest + alembic upgrade

## 关键文档

- [README](../README.md)
- [MAP-RUNTIME-WAKER.md](../docs/MAP-RUNTIME-WAKER.md)
- [AGENTS.md](../AGENTS.md)
