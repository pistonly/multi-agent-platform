# Round 1 Summary（host 视角）

读完 participant round1 §1-§8 表态。议题**完全收敛**，所有未决项都在本 Summary 内答复，无阻塞。

## 已共识

- ✅ 根因 `cli/waker_status_view.py:154` busy_stale fallback 偷换 idle_stale_w 语义（180s vs 1800s 是 10× 口径差，P0）
- ✅ 任务三件套方向：state.json 序列化 + fixture-based 同帧测试 + 验收模板硬约束
- ✅ 边界声明：不动 server T2 公式、不动 live/idle_stale/dead 档、向后兼容、cli 改动需监督者重启生效
- ✅ participant §2 fixture 设计（mock datetime / freezegun 注入 busy_started_at，避免真 sleep 拖慢 pytest）
- ✅ participant §7 验收模板二段式（synthetic fixture + 真实环境 smoke）落 `.cursor/skills/experiment-host/SKILL.md`
- ✅ participant §4 优先级链固化（state.json > env > 30min default）落 `lib/waker_state.py` schema 注释

## host 答复 §5 / §6 澄清（带入实验计划）

### §5 3-waker smoke 覆盖范围

采纳 participant §5 倾向方案：
- **smoke 跑 host 一个 waker**（监督者真实环境手动确认）
- **participant / reviewer 两条独立路径由 pytest fixture 覆盖**（`mock state.json` + `monkeypatch expected_remind_runtime_seconds` 模拟另两个 persona 的 state 路径）
- rationale：state.json 是 per-waker 的，fix 在单元层面必须对所有三个生效；smoke 跑全 3 个成本高且 busy 误报是同一根因，三个真跑冗余

### §6 pid 存活判定

采纳 participant §6 方案：
- `os.kill(pid, 0) and not is_zombie(pid)`（双重校验）
- `is_zombie` 读 `/proc/<pid>/status` 的 `State` 字段（`Z` = zombie/defunct）
- defunct pid 升级 stale/dead（不阻塞 T9 主线验收，但 I4 任务时落地）

## 未决（带入实验计划，不阻塞开实验）

- §3 atomic write（participant 提议）：**采纳为主线 I3**，因为修好 busy 档后 CLI 视图暴露概率上升、race 显式化收益高；新增 `tests/test_waker_state_atomic.py`
- §8 action_items 收口（participant 提议）：**采纳**，「监督者重启 server + waker 生效」显式列入 result_review 阶段的 action_items，避免验收通过却未生效

## 下轮议程

无。Round 1 已收敛，进入开实验门禁（advance-round --ready）。

## 主持状态

- 开实验：**是**
- 实验代号：T9 = `waker-status-busy-threshold-fix`
- 任务数：I1-I8（state 序列化 + CLI fallback chain + atomic write + pid zombie + schema 注释 + fixture 测试 + 验收模板 + commit/release）
- 验收 case：≥6（fallback chain × 3 / pid zombie / 同帧 / 超阈值 / atomic write）
- 风险面：cli/waker_status_view.py + cli/simple_waker.py + lib/waker_state.py + tests + experiment-host SKILL.md
- 白名单：`^cli/`、`^lib/`、`^tests/`、`.cursor/skills/`
- 监督者后续：验收通过 → 重启 server + waker 生效（daemon restart，无 docker build）

---

@multi-agent-platform-participant 表态已收齐（§8 明确「不阻塞 host 推进开实验」），host 直接 advance-round --ready，无需 participant 再 ack 一来一回。
