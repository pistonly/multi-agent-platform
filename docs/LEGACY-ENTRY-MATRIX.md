# Legacy 入口兼容矩阵（Phase 1）

MAP 本仓库默认协作路径：**Skill + `map` CLI + simple-waker**（`./scripts/start-all-simple-wakers.sh`）。

本矩阵列出 pyproject console scripts、`scripts/*.sh`、文档与测试中对各入口的分类，**Phase 1 只标注不删除**；删除类变更见 Phase 2 独立实验。

## 分类说明

| 分类 | 含义 |
|------|------|
| **主路径** | 新接入与日常开发应使用 |
| **legacy-facade** | 兼容别名或 re-export，行为已转发到主路径 |
| **deprecated** | 仍可用但不应新接入；顶部须有 DEPRECATED 注释 |
| **待删** | Phase 2 候选移除（需确认无外部依赖） |

## pyproject console scripts

| Entry | 模块 | 分类 | 说明 |
|-------|------|------|------|
| `map` | `cli.main` | 主路径 | Persona + CLI 协作入口 |
| `map-server` | `server.main` | 主路径 | API 服务 |
| `map-mcp` | `map_mcp.main` | 独立线 | MCP 验证路径，不在本实验退役范围 |

> **已退役**（v0.10）：`map-runtime-waker` console entry、`cli/runtime_waker.py`
> 模块、`cli/host_worker*.py` 整套及配套测试（`tests/test_runtime_waker*.py`、
> `tests/test_waker_phase2_*.py`、`tests/test_host_worker.py`、
> `tests/test_host_experiment_lifecycle.py`）已删除。默认 waker 为
> `cli.simple_waker`（`./scripts/start-all-simple-wakers.sh`）。
>
> **已退役**（v0.16，优化任务 T20）：`map-participant-bridge` /
> `map-reviewer-bridge` console entry、`cli/participant_worker.py`、
> `cli/reviewer_worker.py` 模块、4 个 `scripts/start-*-bridge*.sh` stub
> 及配套测试（`tests/test_participant_worker_claude_agent.py`、
> `tests/test_reviewer_worker_claude_agent.py`）已删除。bridge 路径自
> simple-waker 全面接管后 `cli/` 内零引用，仅测试持有。共享模块
> `cli/bridge_state.py`、`cli/worker_cycle_log.py` 为 simple-waker /
> orchestrator 主路径使用，保留。

## scripts/*.sh

| 脚本 | 分类 | 说明 |
|------|------|------|
| `scripts/start-all-simple-wakers.sh` | 主路径 | 三 persona simple-waker（默认） |
| `scripts/start-simple-waker.sh` | 主路径 | 单 persona simple-waker |

## 文档

| 文档 | 分类 | 说明 |
|------|------|------|
| `README.md` | 主路径 | 默认 waker 章节指向 `start-all-simple-wakers.sh` |
| `AGENTS.md` / `CLAUDE.md` | 主路径 | simple-waker 为主；bridge 已停用 |
| `docs/MAP-SIMPLE-WAKER.md` | 主路径 | simple-waker 运维说明 |
| `docs/MAP-RUNTIME-WAKER.md` | legacy-facade | legacy runtime-waker 参考（如仍存在） |

## 测试

| 区域 | 分类 | 说明 |
|------|------|------|
| `tests/test_simple_waker*.py` | 主路径 | simple-waker 行为 |
| `tests/test_waker_should_wake_action_item.py` 等 | 主路径 | escalation 决策测试，已改从 `cli.action_item_escalation` 导入 |

## CI manifest（check-deprecated.sh）

以下 **deprecated** 条目在 Phase 1 必须仍存在（标注不删）。若删除须同步更新本清单与文档。

check-deprecated.sh 同时执行两条防回潮规则（实验 124e9a00 A3）：manifest 中 `scripts/*.sh` 必须保持 stub（改回真脚本 → CI fail）；退役声明处（CLAUDE.md / Skill wake.md）引用的启动路径必须在本矩阵 scripts 节登记（未登记 → CI fail）。**本文件的 scripts 表格与 DEPRECATED 清单是登记单一真相，改格式前先过 CI。**

（T20 后本清单暂无在册 DEPRECATED 条目——bridge 簇已整体退役，如未来再引入 deprecated 入口按上格式登记。）

## v1.0 移除时间表（T40，2026-09-01 评估）

console scripts 层当前 3 个入口（`map` / `map-server` / `map-mcp`）全部为主路径或独立线，**无 deprecated entry point，v1.0 无需移除计划**。仍处于 warning 级 deprecated（未删、不阻断）的兼容面如下，按「连续两个 minor 版本无使用告警即移除」执行，最迟 v1.0：

| 兼容面 | 位置 | 迁移目标 |
|--------|------|----------|
| CLI `--format legacy` 值 | `cli/main.py`（`--format` / `MAP_CLI_FORMAT`） | `--format table` |
| JSON 字段别名 `advance_round_pending_since` | `cli/runner.py` `_DEPRECATED_ALIAS_RENAMES` | `stale_since` |
| JSON 字段别名 `partition_visibility` | `cli/runner.py` `_DEPRECATED_ALIAS_RENAMES` | `visibility` |
| API `?page_size=` 查询参数 | `server/api/experiments.py` 列表端点（已带 `Deprecation`/`Sunset: v0.12` 响应头） | `?limit=` |
| Python 类 `MapCommandClient`（subprocess 客户端） | `cli/map_command_client.py`（T24 标记 deprecated；simple-waker 与 e2e 已迁 `MapSdkClient`） | `cli.map_sdk_client.MapSdkClient`（保留用途：waker `--subprocess-client` 回退 + 测试注入面） |

## 迁移指引

```bash
# 默认：三 persona waker
./scripts/start-all-simple-wakers.sh

# 一键推进话题直到 open topic 清零
./scripts/start-all-simple-wakers.sh --drain-topics

# 单 persona
./scripts/start-simple-waker.sh --persona host
```

bridge 系入口（`start-*-bridge*.sh` / `map-*-bridge` console entry）已全部删除，勿在新接入中引用；自动推进统一走 simple-waker。
