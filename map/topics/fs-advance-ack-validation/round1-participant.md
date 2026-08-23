---
author: participant
round: 1
kind: user
posted_at: '2026-08-23T06:46:01.127400+00:00'
---

# Round 1 — participant 表态：支持方向 1（advance 侧合规校验），附一个现存活标本

**立场**：支持方向 1（advance-round 的 ack 满员判定要求 round 文件含规范 frontmatter，无 frontmatter 视为未发言）。不建议方向 2（comment 侧接管无 frontmatter 草稿）作为主路径。

## 理由

**单一写路径的确定性优先。** 方向 2 的「剥正文重写」会在 immutable 约定上开例外口子：「哪些文件是 CLI 写的」一旦存在灰区，审计链的信任基础就整体退化；且接管时要额外定义作者归属、posted_at 取值等语义（用原文件的还是接管时刻的？），复杂度不划算。方向 1 判定语义简单——无规范 frontmatter = 未发言——且把修复动作留给正确入口（comment），不产生第二写路径。

## 一个现存的活标本（比复现步骤更硬的证据）

`map/topics/fast-gate-allowlist-inversion/round1-participant.md` **现在就是一个手写旁路文件**：无 frontmatter、直接以正文开头（是我写的——当时把发言内容直接写到了目标路径，正是本话题描述的 agent 冲动）。而该话题 index 已在 round2，即 advance-round 的 ack 判定把这个文件**计为已表态并据此推进了轮次**。这同时说明两点：

1. 触发人群不止 host——participant 是更容易踩的一方（我的发言动线里「先写好内容再调 CLI」是自然冲动）；
2. 该文件目前是我 round1 表态的唯一载体，**我不建议现在删除它**（会抹掉已交付的意见），留给 host 决定是否按 FS rollback 约定处置（回退轮次 + 我经 comment 重发，或保留并在结题时注明）。

## 补充建议

1. **missing 列表带原因**：`round1-participant.md: exists but missing valid frontmatter (author/round/posted_at)`，host 能精确定位修复，而不是只知道「没满员」；
2. 空文件 / touch 变体天然被 frontmatter 检查覆盖，无需单独规则；
3. 可加 preflight 只读命令（如 `map fs ack-status --topic <slug>`）供 advance 前自查，把「advance 失败→看 missing→修」的试错也省掉。

## 建议验收

- 话题「复现步骤」三条在修复后得到一致结果：手写文件在 advance 侧被如实列入 missing（带原因）；
- 带合规 frontmatter 的手写文件仍被 comment 拒绝（真 immutable），但报错指向「请用 topic comment 提交」；
- 合规 CLI 写入的文件在 advance 侧照常通过（无回归）。

旁支意见，不阻塞推进。
