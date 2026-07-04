# v0.8 waker dogfood 探针执行日志

探针实验：`90cba1c3-23c1-4f3a-976c-7640990745f3`  
主实验：`c9776cb4-11f6-406e-baae-de3f259ef11d`  
执行人：multi-agents-platform-host（Cursor agent，waker 标准路径）  
时间：2026-07-01

## 路径声明

- **使用**：`map --persona {host,reviewer}` CLI + runtime-waker（`start-all-wakers.sh`）
- **未使用**：`start-host-bridge*.sh`、`cli/host_worker`、runner stdin/stdout JSON

## 探针全链路（draft → done）

| 时刻 (UTC) | Persona | 动作 | Phase 后 |
|------------|---------|------|----------|
| 10:18:33 | host | `experiment create --submit-for-review` | review |
| 10:23:44 | reviewer | `experiment review add`（0 unreasonable） | review |
| 10:25:46 | host | `experiment approve` | approved |
| 10:25:47 | host | `experiment start` | running |
| 10:26:* | host | 本 log + `experiment complete` | done |

## Waker 稳定性证据

- `.map/waker-logs/host.log` 近期 `cycle_summary`：`wake_errors: 0`（见 host waker 运行段）
- 三 persona waker 由 `start-all-wakers.sh` 常驻（tmux `map` session）
- 评审后 host `experiment_lifecycle` fingerprint 修复已生效（`open_unreasonable_count` 入 todos）
- **bridge 进程**：执行时 `pgrep host_worker|start-host-bridge` 无匹配

## 主实验 c9776cb4 验收摘要（plan v3）

| 项 | 状态 |
|----|------|
| I1 ROUND_SUMMARY_RE 清理 | ✅ |
| I3 systemd unit | ✅ |
| I4 advance-round ack | ✅ |
| SSE A/C | ✅ |
| pytest（见下） | ✅（排除 test_experiment_lock 模块名冲突项） |

## pytest

```bash
pytest tests/ --ignore=tests/test_experiment_lock.py -q
```

## 结论

P3 waker dogfood 探针走通 draft → review → approved → running → complete；主实验满足 v3 完成定义，可 `experiment complete`。
