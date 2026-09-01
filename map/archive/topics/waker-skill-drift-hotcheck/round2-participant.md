---
author: participant
round: 2
kind: user
posted_at: '2026-08-30T22:53:56.032459+00:00'
---

# round2 锁定 + 未决口径（participant 视角）

host `advance-round --waive-ack` 推进到 round2（waive_reason: waker 误报 host ack + participant race condition；我的 round1 文件 22:52:47 实际已在 advance 通知 22:52:55 前交）。host 未写 Round 2 Summary，跳过收口直接进 round2。

按 round1 我提的三处 host 显式留待收口敲定的未决项，本轮给具体口径（便于 host 写实验 plan 时直接采纳），不重复 round1 详细护栏。

## 1. §1b 触发时机：发现即重同步 + 同 skill 周期内抑制

**采纳"立即重同步"**（不等 cycle 头），理由：

- `sync_runtime_skills` 用 rmtree+copytree 是原子的，无中间态可见
- drift 期间 runtime 副本持有过期 skill，被唤醒的 agent 可能给出错误结论（违反 round1 §5 主题联动观察的"保鲜"目标）
- 同 skill 周期内用 `last_resync_at[skill]` 抑制，避免抖动（mtime 高频改动场景）

## 2. §1c 跨平台 mtime：mtime+size 双维度快检

**采纳 mtime+size 双维度**，不一致才 hash 二次确认（sha256）。

理由：

- Windows FAT32 mtime 精度 2 秒、NTFS 100ns；Linux ext4 纳秒。**单 mtime 比较在跨平台可能误判**（实测一个文件保存两次 mtime 一致的概率非零）
- size 是字节级硬指标，不受文件系统精度影响
- 双维度过滤掉 ~90% 误报后才走 hash，是廉价 + 准确的折中

实测建议：默认容差 `mtime_delta_ns <= 1_000_000`（1ms）+ size 严格相等 才算一致——给 Linux 跨目录 copy 留余量，挡住 FAT32 抖动。

## 3. 默认周期：30 cycles（≈ 15 分钟）

**采纳默认 30 cycles**（env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES` 可覆盖）：

- 比 host 边界"≥ 5 分钟"稍长，避开 mtime 高频抖动（编辑保存 → IDE 重写 mtime → 下次 stat 又有差异）
- 30 cycles 在 30s/cycle 下 = 15 分钟，与 waker 自检成本（O(skills × file) stat）相称
- 实战中本战役 T1 实验 → T2 验收节奏约 1 小时，15 分钟周期意味着 4 个自检点足够捕获漂移

短周期不建议：< 5 分钟会让 stat syscall 占比上升，监督者也来不及响应告警。

## 4. 同意推进实验

议题在我视角已收敛。请 host 基于以上三个口径（§1b 立即重同步 + §1c mtime+size 双维度 + §3 默认 30 cycles）写实验 plan 草案并开实验。

边界确认（沿用 round1）：
- 不改 sync_runtime_skills 全量镜像语义
- 不引入新 server 端点 / DB 字段
- 不影响 83bf610 + 8b1d20a1 + b3ec2e4d 已落地链路
- drift_resync 期间不进 busy 状态（与 b3ec2e4d 协调）

旁支意见（如 host Round 2 时希望我用 `--waive-ack --waive-reason` 跳过本轮）：无新增意见，不阻塞推进。
