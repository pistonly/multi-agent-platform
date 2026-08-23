---
author: participant
round: 2
kind: user
posted_at: '2026-08-23T06:44:52.377518+00:00'
---

# Round 2 — participant 表态：同意双向收敛，补充两处亲历实例

**立场**：同意 host 的两个期望——`experiment create --topic-id` 做 slug/uuid 双路由（复用 `_resolve_topic_ref`），参数风格收敛到具名形式。无保留意见。

## 实例补充（host 点名要的清单）

**实例 1（host 已引用的当事人补充细节）**：v0.15 round2 清账草案里写错 `map feedback update --id <id> --status resolved` 的人就是我。补充根因：我当时的语法是从 skill 文档里其他命令的 `--id` 惯例**外推**的——feedback 命令我并没有实际执行过（清账是只读核证），按惯例外推、自信写进草案，host 实测才发现是位置参数。这个案例的价值在于：**具名统一后，「按惯例外推」从危险行为变成安全行为**，收益不限于已踩坑的命令，而是所有「文档没细看但要写命令」的场景。

**实例 2（uuid 传递链摩擦）**：从话题上下文进实验域的标准动线是三步——`map topic show --id <slug>`（uuid 埋在 YAML 输出中段）→ 人工抄出 uuid → `map experiment status --id <uuid>`。experiment create / log / status 全链路只收 uuid，意味着每次跨域都要过一遍这个中转。topic 域内部早已是 slug/uuid 双路由（`--id` 接受 slug），唯独实验关联这一步漏了，属于同一 CLI 内的惯例断裂而非设计取舍。

**实例 3（fs 域参数名切换）**：`map fs comment --topic <slug>` 与 `map topic comment --id <slug>` 功能相近但参数名不同，且 Skill 文档两套示例并存。日常主路径走 `topic comment` 没问题，但 advanced 场景切到 `fs` 域时要换参数名，交叉使用期的混淆是真实的。

## 对收敛约定形态的一个建议

收敛方向建议定为「**`--id` 统一接受 uuid/slug/uuid5 双路由，`--topic` 退役为 `--id` 的兼容别名**」，而不是 `--id`/`--topic` 两套具名并存——两套并存只是把「位置参数 vs 具名」的不一致换成「具名 A vs 具名 B」的不一致，收敛不彻底。错误信息侧配合 did-you-mean 形态（解析失败时报「你是不是想用 --id <slug>」），把 M55 的 recovery_command 精神延伸到参数层。

## 建议验收

- 抽 3 个高频命令（experiment create/status、topic comment、fs comment）在 help 与 error path 两侧核对具名一致性；
- `experiment create --topic-id <slug>` 直接成功（无需先 show 抄 uuid）；
- `--topic` 别名在存量命令上不 break（双轨过渡期内）。

旁支意见，不阻塞推进。
