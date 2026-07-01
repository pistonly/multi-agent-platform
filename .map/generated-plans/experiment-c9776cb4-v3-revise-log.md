# v0.8 实验 plan v3 修订记录

实验：`c9776cb4-11f6-406e-baae-de3f259ef11d`  
执行人：multi-agents-platform-host  
时间：2026-07-01

## 动因

团队确认 **bridge/runner 路径已废弃**，标准协作为 `runtime-waker` + Agent 直执行 + `map` CLI。v2 plan 中 I2、bridge dogfood、I4 的 host_worker 调用点与主线冲突。

## 修订摘要（v2 → v3）

| 变更 | 说明 |
|------|------|
| **取消 I2** | 不再维护 `map_runner_io` / 6 份 runner 解析抽象 |
| **取消 bridge dogfood** | 完成条件改为 waker dogfood 全链路 |
| **I4 重定向** | server/CLI 不变；host 执行改 **topic-host Skill + map CLI**，不再要求改 `host_worker` |
| **I1 标记已完成** | log #1 已落地；不要求 bridge 验收 |
| **保留** | I3（systemd wakers）、SSE A/C |

Plan 文件：`.map/generated-plans/experiment-c9776cb4-v3-plan.md`（MAP plan version **3**）

## 平台改动（附带）

`running` phase 原先无法 `plan revise`（422）。已放宽 `server/services/plan_service.py` 允许 **draft / review / running** 三阶段修订，并已重建 API 容器。

## 本地待清理

- 回退未提交的 I2 草稿：`sdk/python/map_runner_io.py`、`tests/test_map_runner_io.py`、6 个 runner 改动、`pyproject.toml` 中 `map_runner_io*` package

## 下一步

按 v3 执行顺序：P1 **I3 + I4** → P2 **SSE A/C** → P3 **waker dogfood** + status_md v9。
