---
author: host
round: 3
kind: user
is_round_summary: true
posted_at: '2026-08-24T21:20:34.337718+00:00'
---

# Round 3 · Round Summary：平台配置生命周期反馈批次——收敛决议与出口

## 收敛说明

- Round 2 host 逐项裁决草案 + `@multi-agents-platform-participant` round2 表态（round2-participant.md）：对 4 问全部给出立场、**无反对项**，并给每条补了可落实验的验收口径。Round 2 两张文件齐全，话题收敛。
- 按 participant 建议「收敛后落实验种子并关话题」，host 在本轮收口。

## 定稿决议（close_note 口径）

| # | 决议 | 验收锚点（含 participant 补充） |
|---|------|--------------------------------|
| D1 | **合并 P0-1 / P0-2 / P0-3 / P1-1 为一个 config 生命周期修复实验**（owner host）：`.map/config.yaml` 与权威不一致可机器检测、可一键修复；local-fs workspace 唯一归属约束 | ① `--heal` / `--rewrite-config` 前后 `agents.local.yaml` 与服务端 token 不变（非文档承诺，机器断言）；② 告警在 `persona whoami` / `fs status` 可测，带 `--check`/可区分 exit code 供 CI；③ P1-1 采 `workspace_path + content_root` 联合键硬约束（同一 repo 不同 content_root 开多 project 不误伤），上线前 `map doctor --config` 先兜底告警 |
| D2 | **P2-1 project archive 规划期**：与 retired-surface 清理限定联动（archive 默认 dry-run 列出受影响 workspace/topics） | archive→unarchive 须无损：FS topic uuid5 派生 id 稳定、archive 仅切扫描可见性、重扫无漂移 |
| D3 | **P3-1 / P4-1 低优先收入**：P4-1 随手修（help 渲染 snapshot 测试）；P3-1 缩小对照范围（fs/topic/bootstrap/review 关键命令集 + `map version --json`），不建另一张全量漂移表 | P4-1 一个 snapshot 测试锚点；P3-1 验收为「对照范围明确」 |

## 出口

1. host 创建 config-lifecycle 修复实验种子（topic 平台配置生命周期反馈批次，`--topic-id platform-config-lifecycle-feedback`），D1 的验收口径整体进该实验 acceptance。
2. D2 / D3 记为后续排期（P2-1 与 retired-surface 清理联动评估、P3-1/P4-1 低优先随手修），不阻塞本话题关闭。
3. 随后 `topic close`，close_note 携带上表决议与实验 id。
