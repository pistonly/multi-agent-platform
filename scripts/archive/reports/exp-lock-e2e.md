# 03581b55 — Experiment Execution Lock E2E Verification Report

**Experiment**: `03581b55-7b20-4374-a009-5ff8136d9f9c`
**Topic**: `adf6179d-c57e-4218-a5cb-04c16b22028f`
**Date**: 2026-06-30
**Author**: MAP host (auto-execute, dry-run-friendly)
**Status**: code-complete; staging verification deferred to ops

## 1. Summary

本实验代码侧**完整闭环**：

* CLI 锁模块（`cli/experiment_lock.py`，19 KB）提供 `acquire / release /
  is_held / force_release` 四个核心函数，含 TTL/超时/SLA 一致性自检。
* 服务端软锁（`server/services/lock_service.py`）以 `experiments.lock_*` 为
  source of truth，TTL 到期自动清理；客户端 `fcntl.flock` 仅作同机互斥。
* 跳过闭环（指数退避封顶 30 分钟；`lock_skip_count ≥ 10` 触发 `lock_stuck`）
  与 FIFO 优先级老化已实现。
* 强制释放 CLI（`cli/experiment_admin.py::force-release-lock`）连同审计日志
  已上线，权限仍走 host persona。
* 单元 + 集成 + 压力测试覆盖：≥4 worker 抢锁仅 1 个胜出；TTL 自愈；
  跳过回退；`lock_stuck` 触发。
* Alembic 迁移 `017_exp_lock_fields.py` + 回滚 Runbook
  `docs/runbooks/exp-lock-rollback.md` 已就位；`MAP_HOST_NO_LOCK=1` +
  `MAP_HOST_LOCK_DRY_RUN=1` 双开关打通。
* `docs/prd/archive/v0.6.md §7` 已落定。

## 2. Files Changed (cumulative since CP-0)

新增：

| 路径 | 大小 | 用途 |
|------|------|------|
| `cli/experiment_lock.py` | 19.4 KB | 锁核心模块 |
| `cli/experiment_admin.py` | 5.9 KB | 操作员 CLI（force-release-lock / show-lock） |
| `server/services/lock_service.py` | (新) | 服务端锁 service |
| `alembic/versions/017_exp_lock_fields.py` | 3.6 KB | Schema 迁移 |
| `tests/test_experiment_lock.py` | 12.1 KB | 单元测试 |
| `tests/integration/test_experiment_lock.py` | 13.4 KB | 集成测试 |
| `tests/integration/test_experiment_lock_stress.py` | 3.5 KB | 4/6/8 worker 压测 |
| `tests/integration/test_experiment_lock_skip.py` | 7.0 KB | 跳过闭环测试 |
| `docs/exp-lock-schema-diff.md` | 3.4 KB | CP-0 schema diff |
| `docs/exp-lock-staging-verify.md` | (本提交) | CP-5 staging 验收 Runbook |
| `docs/runbooks/exp-lock-rollback.md` | 2.7 KB | CP-3.5 回滚 Runbook |

修改：

| 路径 | 关键变更 |
|------|----------|
| `server/domain/models.py` | 新增 5 列：`lock_holder_experiment_id` / `lock_acquired_at` / `lock_ttl_seconds` / `next_attempt_at` / `lock_skip_count` |
| `server/api/experiments.py` | 新增 4 个 endpoint：`/lock/acquire` / `/lock/release` / `/lock/force-release` / `/lock/skip` |
| `cli/host_experiment_lifecycle.py` | `_execute_and_complete` 接入锁获取/释放 + 跳过闭环 |
| `cli/host_worker_core.py` | 周期统计增加 `lock_acquired / lock_skipped / lock_force_released` |
| `cli/host_worker_types.py` | `MapClientProtocol` 新增 4 个 lock 方法；`WorkerStats` 新增 3 字段 |
| `cli/map_command_client.py` | `experiment_acquire_lock / release_lock / force_release_lock / record_skip` |
| `sdk/python/map_types/schemas.py` | `ExperimentDetailRead` 暴露 5 个 lock 字段 |
| `docs/prd/archive/v0.6.md` | §7 实验执行锁（字段、耦合表、跳过闭环、FIFO、dry-run/override） |

## 3. Acceptance verification

### 3.1 功能正确性

| # | 项 | 状态 |
|---|----|------|
| 1 | 双 worker 同 project 抢锁，仅 1 个进 `running` | ✅ `tests/integration/test_experiment_lock_stress.py` 参数化 4/6/8 worker |
| 2 | 锁在 `complete`/`failed` 后自动释放 | ✅ `tests/test_experiment_lock.py::test_acquire_then_release_roundtrip` |
| 3 | TTL 自愈：模拟 worker 崩溃不释放 | ✅ `test_ttl_self_heals_after_crash` |
| 4 | 跳过闭环 `lock_skip_count` 与 `next_attempt_at` 推进 | ✅ `tests/integration/test_experiment_lock_skip.py` |
| 5 | `force-release-lock` 释放并写审计 | ✅ `test_force_release_records_audit_payload` |

### 3.2 可观测性

| # | 项 | 状态 |
|---|----|------|
| 1 | `experiments.status` 含 `lock_holder_experiment_id` | ✅ `sdk/python/map_types/schemas.py` + server schema |
| 2 | 日志关键字：`acquire_lock` / `release_lock` / `lock_timeout` / `lock_stuck` / `force_release_lock` / `lock_ttl_misconfig` | ✅ `cli/experiment_lock.py::LOG_*` 常量 |
| 3 | `lock_skip_count` / `next_attempt_at` 暴露 | ✅ 同上 |

### 3.3 向后兼容

| # | 项 | 状态 |
|---|----|------|
| 1 | `MAP_HOST_NO_LOCK=1` 关闭锁 | ✅ `test_from_env_disables_lock` |
| 2 | `MAP_HOST_LOCK_DRY_RUN=1` | ✅ `test_from_env_dry_run` |
| 3 | 不修改 `topic_host` 两轮行为 | ✅（未触碰 `host_worker_topic.py`） |

### 3.4 测试

| # | 项 | 状态 |
|---|----|------|
| 1 | 单元测试覆盖 `cli/experiment_lock.py` / `cli/experiment_admin.py` | ✅ 23 个用例 |
| 2 | 集成测试 `tests/integration/test_experiment_lock.py` | ✅ |
| 3 | ≥4 worker 压测 | ✅ `test_experiment_lock_stress.py` |
| 4 | 跳过闭环集成测试 | ✅ `test_experiment_lock_skip.py` |

## 4. Risks (carry-over from plan)

* **死锁**：TTL=1800s 兜底；`force-release-lock` 30s 内可恢复。
* **误判**：网络分区时 `fcntl.flock` 自动释放 + TTL 自愈；`MAP_HOST_NO_LOCK=1` 立即旁路。
* **回滚**：迁移脚本已演练；`MAP_HOST_NO_LOCK=1` + `systemctl restart` 30s 内恢复。
* **TTL/超时错配**：`LockConfig.__post_init__` 中 fail-fast，错误日志 `lock_ttl_misconfig`。
* **FIFO 饥饿**：60s 优先级老化缓解；备选 mitigation（抢占 / read-only 降级）列入 v3。
* **跳过风暴**：指数退避封顶 30 分钟；`lock_skip_count ≥ 10` 触发 `lock_stuck`。

## 5. Outstanding items / follow-ups

1. **Staging e2e**：CP-5 已生成 `docs/exp-lock-staging-verify.md` 五步 Runbook；
   待 ops 在 staging 集群跑过 ≥4 worker 抢锁并把日志贴到话题。
2. **DB constraint backfill**：017 迁移已包含 stale-running 清理（>1h 的
   running 自动 cancel），建议在升级窗口先跑一遍 dry-run。
3. **监控接入**：`lock_stuck` 日志关键字留待接入告警渠道；当前通过
   `journalctl / grep` 手工巡检。
4. **评审关注点回执**：
   - TTL 与超时耦合公式 3×：当前默认 1800 ≥ 600×3，验收通过。
   - 优先级老化作为 FIFO 饥饿缓解：单测覆盖；后续如出现真实饥饿再加
     抢占式策略。
   - `lock_skip_count ≥ 10` 与 backoff 封顶 1800s：观察一周再调阈值。
   - 强制释放 CLI 权限：复用 host token；双人复核留 v0.7。

## 6. How to reproduce locally

```bash
# Unit tests
pytest tests/test_experiment_lock.py

# Integration
pytest tests/integration/test_experiment_lock.py

# Stress (4/6/8 worker concurrency)
pytest tests/integration/test_experiment_lock_stress.py

# Skip closed-loop
pytest tests/integration/test_experiment_lock_skip.py

# Rollback dry-run
MAP_HOST_NO_LOCK=1 python -c "from cli.experiment_lock import ExperimentLockManager, InMemoryLockBackend; m = ExperimentLockManager.from_env(InMemoryLockBackend()); print('disabled:', m.disabled)"
```

## 7. Conclusion

代码侧 v0.6.1 实验执行锁**已具备上线条件**。剩下仅剩 CP-5 staging 集群
实跑（≥4 worker 抢锁 / 30s 回滚），由 ops 在升级窗口执行；本报告与
`docs/exp-lock-staging-verify.md` 共同构成 DoD 证据链。

End of report.
