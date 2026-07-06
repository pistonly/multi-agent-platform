# Legacy 入口兼容矩阵（Phase 1）

MAP 本仓库默认协作路径：**Skill + `map` CLI + simple-waker**（`./scripts/start-all-wakers.sh`）。

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
| `map-participant-bridge` | `cli.participant_worker` | deprecated | 旧 participant bridge；用 simple-waker + participant Skill |
| `map-reviewer-bridge` | `cli.reviewer_worker` | deprecated | 旧 reviewer bridge；用 simple-waker + reviewer Skill |
| `map-runtime-waker` | `cli.runtime_waker` | legacy-facade | 模块为 re-export 兼容层；默认 waker 为 `cli.simple_waker` |
| `map-mcp` | `map_mcp.main` | 独立线 | MCP 验证路径，不在本实验退役范围 |

## scripts/*.sh

| 脚本 | 分类 | 说明 |
|------|------|------|
| `scripts/start-all-wakers.sh` | 主路径 | 三 persona simple-waker（默认） |
| `scripts/start-all-simple-wakers.sh` | 主路径 | 同上（被 start-all-wakers 调用） |
| `scripts/start-simple-waker.sh` | 主路径 | 单 persona simple-waker |
| `scripts/start-runtime-waker.sh` | legacy-facade | 名称历史遗留；实际 `exec` 到 `start-simple-waker.sh` |
| `scripts/start-participant-bridge.sh` | deprecated | 旧 bridge 启动脚本 |
| `scripts/start-participant-bridge-claude.sh` | deprecated | 旧 bridge（Claude runner） |
| `scripts/start-reviewer-bridge.sh` | deprecated | 旧 bridge 启动脚本 |
| `scripts/start-reviewer-bridge-claude.sh` | deprecated | 旧 bridge（Claude runner） |

## 文档

| 文档 | 分类 | 说明 |
|------|------|------|
| `README.md` | 主路径 | 默认 waker 章节指向 `start-all-wakers.sh` |
| `AGENTS.md` / `CLAUDE.md` | 主路径 | simple-waker 为主；bridge 已停用 |
| `docs/MAP-SIMPLE-WAKER.md` | 主路径 | simple-waker 运维说明 |
| `docs/MAP-RUNTIME-WAKER.md` | legacy-facade | legacy runtime-waker 参考（如仍存在） |

## 测试

| 区域 | 分类 | 说明 |
|------|------|------|
| `tests/test_simple_waker*.py` | 主路径 | simple-waker 行为 |
| `tests/test_runtime_waker*.py` | legacy-facade | 兼容层 / 迁移期测试 |
| bridge / host_worker 相关 | legacy-facade | 保留至模块拆分完成 |

## CI manifest（check-deprecated.sh）

以下 **deprecated** 条目在 Phase 1 必须仍存在（标注不删）。若删除须同步更新本清单与文档。

DEPRECATED: scripts/start-participant-bridge.sh
DEPRECATED: scripts/start-participant-bridge-claude.sh
DEPRECATED: scripts/start-reviewer-bridge.sh
DEPRECATED: scripts/start-reviewer-bridge-claude.sh
DEPRECATED: cli/participant_worker.py
DEPRECATED: cli/reviewer_worker.py

## 迁移指引

```bash
# 默认：三 persona waker
./scripts/start-all-wakers.sh

# 单 persona
./scripts/start-simple-waker.sh --persona host
```

勿再使用 `start-*-bridge*.sh` 或 `map-*-bridge` console entry 做新接入。
