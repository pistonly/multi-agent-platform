"""Waker status threshold constants — single source for cli + server.

派生自 ``SimpleWakerConfig.active_interval`` / ``idle_interval``（cli 端配置，
seconds 单位）+ server ``status_service`` 常量。``cli/waker_status_view.py`` 与
``server/services/status_service.py`` 都从此模块导入，避免阈值硬编码副本漂移。

实验 T6（bd057f92 / a8b64c20）修复 T5-A（715202a3）上线即误报 stale：waker 以
active_interval=30s 正常轮询 + poll 间隙常态 26-51s，``map work`` 三 persona 全 ok，
同时刻 ``map waker status`` 标 stale。同帧不一致的根因是 ``cli/waker_status_view.py:26
LIVE_WINDOW_SECONDS = 30`` 硬编码——阈值与轮询周期同量级导致高误报。本模块把阈值口径
收敛到一处，派生自 SimpleWakerConfig（in-memory 配置）而非 state.json（T5-A I2 未序列化
active_interval/idle_interval 字段）。

## §派生公式设计理由

为什么 ``LIVE_WINDOW_SECONDS = max(2 × active_interval, 30)`` 用 2× 而不是 1.5× 或 3×：

- **1.5× 不选**：active_interval=30s 时 1.5×=45s，waker 实际 poll 间隙常态 26-51s
  （含网络抖动），1.5× 容不下常态抖动，会把「正在跑」误判 stale
- **3× 不选**：active_interval=30s 时 3×=90s，stale 告警窗口 60s 才升级 dead，
  期间真实 dead 的 waker 不会被及时发现，失去告警意义
- **2× 是折中**：active_interval=30s 时 2×=60s，stale 窗口 30s（30-60s），
  既抗单次网络抖动又不过于迟钝；60s 仍能及时升级 dead
- **floor 30s 配合**：active_interval 极短场景（5s）下，2×=10s 太短不可用，
  floor 30s 给突发延迟留余量

## 口径三层

T6 闭环后本战役「口径三层」形成完整闭环：

| 层 | 落地 | 职责 |
|----|------|------|
| 状态机口径 | T2 (b3ec2e4d) | server ``status_service.py`` 定义「什么算活/死/busy 容忍」 |
| 视图渲染口径 | T5-A (715202a3) + **T6（本实验）** | cli ``map waker status`` 复用状态机口径派生阈值（不再硬编码 30s） |
| 视图数据源口径 | T5-A A1 + d0c9dc5f | waker state.json 单源 + drift 自检保鲜 |

硬约束：
- 视图层**不能**定义自己的阈值常量，必须派生自状态机层
- ``lib/waker_status_config.py`` 单模块，cli + server 都 import
- 视图类实验 plan §验收 必含「同帧一致性测试」（T5-A 闭环遗漏硬约束，写入 SKILL/wake.md）
"""
from __future__ import annotations


def live_window(active_interval: int) -> int:
    """gap ≤ 此值视为 live（cycle 周期内正常轮询）。

    派生公式：``max(2 × active_interval, 30)``
    单位：seconds（SimpleWakerConfig.active_interval 是 seconds）。
    """
    return max(2 * active_interval, 30)


def idle_stale(active_interval: int, idle_interval: int) -> int:
    """idle 状态 stale 阈值（未配置 idle 时的 fallback）。

    派生公式：``max(3 × active_interval, idle_interval)``
    """
    return max(3 * active_interval, idle_interval)


def dead_window(active_interval: int) -> int:
    """gap > 此值视为 dead（远超 1+ idle stale 窗口）。

    派生公式：``10 × active_interval``
    """
    return 10 * active_interval


def busy_stale(expected_remind_runtime: int, idle_threshold: int) -> int:
    """busy 容忍阈值（seconds；server 端 minutes 输入由调用方转换）。

    派生公式：``max(expected_remind_runtime, 2 × idle_threshold)``
    busy 期间 polling cycle 暂停属正常；超过容忍才标 stale。
    """
    return max(expected_remind_runtime, 2 * idle_threshold)
