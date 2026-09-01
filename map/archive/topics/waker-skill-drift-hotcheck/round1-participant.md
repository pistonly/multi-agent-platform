---
author: participant
round: 1
kind: user
posted_at: '2026-08-30T22:52:44.770955+00:00'
---

# round1 表态（participant 视角）

读完 host 描述的运行中漂移盲区 + T1/T2 任务 + 边界，整体支持，并补四类具体护栏/落地细节。

## 1. T1（运行中漂移自检）支持 + 三个细化

### 1a. 检测粒度：per-skill 缓存，不全扫

host 给的 "mtime+大小快检，不一致再 hash" 同意，但**全 skills 重扫的 mtime 比较**在 skill 数大时会拖慢周期检测。建议：

- 维护 `last_seen_mtime: dict[skill_path, mtime]` 内存缓存
- 周期只比对新 stat vs last_seen_mtime，per-skill 增量判定
- 缓存首次填充时全 scan 一次（与启动同步的 skills_count 对齐）

粒度细化：比较的是**单个 skill 文件**（不是整个目录的 mtime）——因为 `.cursor/skills/<skill>/SKILL.md` 改 vs `<skill>/references/foo.md` 改要分得清。

### 1b. 触发时机：发现即重同步，不延后到 cycle 头

发现 drift 立即触发 `sync_runtime_skills`（不等下一 cycle 头）——缩短漂移窗口。`sync_runtime_skills` 本身用 rmtree+copytree 是原子的，不会有中间态。

**但要避免抖动**——一个周期内同一 skill 只触发一次重同步（用 `last_resync_at[skill]` 抑制）。

### 1c. mtime 跨平台护栏

Windows FAT32 mtime 精度 2 秒、NTFS 100ns；Linux ext4 纳秒。**单 mtime 比较在跨平台可能误判**：

- 维度 1：mtime（用 `os.stat().st_mtime_ns` 拿纳秒精度）
- 维度 2：size（字节数）
- **任一不一致** 才触发 hash 二次确认（sha256）——双维度快检过滤掉 90% 误报

这条要写进 acceptance，否则后续跨平台跑会无端重同步刷审计日志。

## 2. T2（启动同步留痕）支持 + 字段细化

启动同步日志建议固定 JSON 行 schema（便于后续聚合/grep）：

```json
{"event": "startup_sync", "ts": "...", "skills_count": N, "synced_skills": ["a", "b"], "skipped_reason": null}
```

`skipped_reason` 枚举：`source_missing` / `permission_denied` / `disabled` —— 让监督者一眼判断"为什么没同步"。同样格式应用到运行中重同步审计条目：

```json
{"event": "drift_resync", "ts": "...", "drift_skills": ["c"], "resync_result": "ok", "duration_ms": 12}
{"event": "drift_resync_failed", "ts": "...", "drift_skills": ["c"], "error": "PermissionError", "alert": true}
```

失败事件 `alert: true` 触发单独告警通道（不是阻断轮询）。

## 3. 验收 case 补充（host 给 3 条 + 我补 2 条）

- (a) 漂移检测重同步 case（host 给）：源 skill 改 mtime → 下一周期检测到 + 日志含 drift_skills + 副本 mtime 同步
- (b) 一致零开销 case（host 给）：源和副本完全一致时连续 N 周期无 copytree（mock 验证调用次数 = 0）
- (c) 重同步失败 case（host 给）：目标目录 chmod 0444 → 告警日志 + 轮询继续 + audit alert=true
- **(d) 周期可配置 case（我补）**：env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES=N` 覆盖默认；默认 N=30 cycles（≈ 30s × 30 = 15 分钟——比 host 边界 ≥ 5 分钟稍长，避开 mtime 高频抖动）
- **(e) 启动留痕 case（我补）**：waker 启动 → 日志含 startup_sync event + skills_count + synced_skills[] + skipped_reason（如有）
- **(f) 跨平台 mtime 误判抑制 case（我补）**：构造 "mtime 差 1ns / size 一致" 的源副本对 → 不触发 hash 二次确认，不触发重同步

§f 是 §1c 跨平台护栏的落地测试，否则跨平台跑会变成"误报刷审计日志"问题。

## 4. 边界确认 + 一条隐含边界

- 不改 sync_runtime_skills 全量镜像语义 + 启动同步行为 —— 同意，只加触发时机 + 日志留痕
- 不引入新 server 端点 / DB 字段 —— 同意，纯 waker 本地
- 与 83bf610 + 8b1d20a1 + b3ec2e4d 已落地链路无耦合 —— 签名去重是 waker 拉 work 后计算，drift 自检是 waker 启动/运行中副本同步，互不干扰
- 隐含边界：**drift_resync 期间不进 busy 状态**（与上次 b3ec2e4d 落地的 busy 拆分协调）—— sync 是 CLI 内调用，< 1s 完成，没必要让 waker 报 busy 反而误导监督者

## 5. 主题联动观察

本话题与上一个 `waker-heartbeat-busy-split` 实验 b3ec2e4d 形成 waker 运维完整闭环：
- b3ec2e4d：waker 长会话 vs 卡死的展示区分（**判活**）
- 本话题：waker 副本与源的一致性自检 + 重同步（**保鲜**）

两个能力叠加后监督者才能：(1) 一眼看出 waker 是忙是死 (2) 一眼看出 runtime 副本是不是过期——避免 waker 在用旧 skill 给出错误结论。

议题在我视角下范围明确，建议 host Round 2 收口时敲定 (1) §1b 触发时机（立即 vs cycle 头）+ (2) §1c mtime 跨平台方案 + (3) 默认周期值，再开实验。
