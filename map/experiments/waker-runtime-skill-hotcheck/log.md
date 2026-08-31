# waker runtime skill 热自检 — 完成日志

## summary

实验 d0c9dc5f-346e-48da-af35-02108befa122 完成。DriftDetector 模块 +
simple_waker 周期自检 + 启动/运行双留痕；9 case 回归测试全过；1791 pytest
passed；ruff check 0；commit 5892a83 窄白名单 ^cli/ ^tests/。

## 实施 log

- I1+I2: cli/drift_detector.py 新建（DriftEntry/ResyncResult dataclass +
  DriftDetector 周期检测 + 1ms mtime 容差 + sha256 二次确认 + 1s resync 抖动抑制）
- I3: cli/wake_backend.py sync_runtime_skills 签名扩展 → tuple[list[str], str | None]
  + cli/simple_waker.py _startup_sync_with_audit helper（4 类 skipped_reason 枚举）
- I4: cli/simple_waker.py SimpleWakerConfig.drift_check_interval_cycles（默认 30）
  + CLI flag + env override + 主循环挂入 _run_drift_check（total.cycles % interval == 0）
  + 4 类审计事件（drift_no_change/drift_resync/drift_resync_failed/drift_check_failed）
- I5: 跨平台 mtime 容差在 DriftDetector 内部完成（1_000_000ns tolerance）
- I6: tests/test_simple_waker_drift_detection.py 9 case 全过；tests/test_orchestrator.py
  mock return_value 改成 ([], None) 适配新签名

## 风险

- sync_runtime_skills 全量镜像语义未改（rmtree+copytree+孤儿清理），仅新增返回 tuple
- 审计日志走 cli.simple_waker.skill_audit logger，与 8b1d20a1 通知 fan-out 链路完全解耦
  （不污染 wakeable notification 通道）
- drift_resync 期间不触发 busy 状态（与 b3ec2e4d 协调；sync < 1s 完成）
- 默认 30 cycles（≈ 15 分钟 @ 30s/cycle），< 5 分钟不建议

## acceptance

- A1: ✓（主循环挂入 + cycle_index % interval + env/flag 双通道）
- A2: ✓（per-skill 单文件粒度 + references/*.md 独立比较）
- A3: ✓（1ms mtime 容差 + size 严格相等 + sha256 二次确认）
- A4: ✓（立即 resync + 1s 抖动抑制 + 失败不抛）
- A5: ✓（4 类 skipped_reason + 固定 JSON schema）
- A6: ✓（4 类审计事件 + alert 通道独立）
- A7: ✓（9 case 全过：tests/test_simple_waker_drift_detection.py）
- A8: ✓（ruff 0 + pytest 1791/2/0）
- A9: ✓（sync 语义不动 + 无 server/DB 改动 + 不影响既有链路）
