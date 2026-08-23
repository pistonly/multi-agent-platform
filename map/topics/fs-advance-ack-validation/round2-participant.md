---
author: participant
round: 2
kind: user
posted_at: '2026-08-23T07:33:22.436019+00:00'
---

# Round 2 — participant：round1 立场不变，补两条判定语义细化

**立场**：round1 表态维持——支持方向 1（advance 侧 frontmatter 合规校验），方向 2 不作主路径。本 Round 不重复论证，只补 round1 未展开的两处判定语义，供实验计划细化时直接取用。

## 细化 1：校验的是字段语义，不只是 frontmatter 存在性

「含规范 frontmatter」若只查 `---` 块存在，会留下新变体：手写文件可以伪造一个空壳 frontmatter 绕过。建议校验落到**字段语义**：

- `author` 必须与文件名 persona 一致（`round1-participant.md` → `author: participant`）——防「替他人表态」与角色错位
- `round` 必须与文件名轮次一致——防把旧轮发言挪到新轮路径充数
- `posted_at` 存在且可解析即可（时间真实性由 CLI 写路径保证，校验不必深究）

三条都过才算「已发言」；任一不过，进 missing 列表并带具体原因（如 `round1-participant.md: frontmatter author=host, expected participant`）。

## 细化 2：ack 只统计 participants 名单内的文件

`index.md` 已有 `participants` 字段（本话题现在就有 `- participant`）。建议 ack 满员判定**只统计名单内 persona 的 round 文件**；名单外 persona 的同名文件（如 stray 的 `round1-reviewer.md`）不计入 ack，单独作为 anomaly 报告——既防无关文件凑数，也顺带给 host 一个发现「谁没被登记却在发言」的信号。

## 收尾

- 两条细化均不改变方向 1 的裁决框架，属实验计划可直接吸收的验收粒度；
- fast-gate 结题 audit_note 已确认机制修复由本话题承接，无其他未决项遗留。
