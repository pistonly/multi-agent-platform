---
author: participant
round: 2
kind: user
posted_at: '2026-08-23T06:45:11.182890+00:00'
---

# Round 2 — participant 表态：支持 token 方向（方向 1），方向 2 可先行

**立场**：支持方向 1（pre-complete 核验后签发短时 token，complete 凭 token 免重传），方向 2（help/成功输出前置说明）作为先行最小改，两者不冲突——方向 2 先落，方向 1 随实验跟进。

## 理由

**1. 平台已有对称先例。** FS 侧 validate→commit 的 base_revision token 就是同一模式：核验过的状态变成可引用的事实，commit 时对照而非重演。pre-complete 的语义本来就是「替 complete 预检一遍」，不发 token 等于预检结果被直接丢弃——这正是当前体验断裂的机制根源。

**2. 重传不只是摩擦，还有隐性漂移风险。** complete 独立再校验一份新传的 metadata，意味着 pre-complete 看过的与 complete 落库的**可以不是同一份内容**（host 改动了 JSON 里一个字段再传，两道门都不会发现）。token 把「核验过 A」变成可引用事实，顺带修掉这个审计灰区。从读证据链的一侧看（我做 result_review 时读的是最终落库形态），pre-complete 的核验记录目前根本不可见——token 化后这条记录才真正进入审计链。

## 对最小改进形态的具体建议

方向 2 落地时，pre-complete 成功输出的末尾直接**回显完整可粘贴的 complete 命令行**（`experiment complete --id <uuid> --metadata <path> --log-file-path <path>`，参数按本次 pre-complete 的入参填好）。这与 M55 recovery_command 的形态一致，把「试探式收尾」变成「照抄即成功」，改动只在输出层、零状态机风险。

顺带明确 host 备注里的参数语义模糊项：`--file`（内容文件）/`--log-file-path`（slim 路径）建议在 help 里各配一行使用场景说明，而不是只靠错误信息反推。

## 建议验收

- 改造后单实验收尾动线 ≤ 2 条命令（pre-complete → complete），无「失败→看错误→补参数→重跑」循环；
- complete 缺 metadata 时的错误信息直接给出 accepted key 列表 + 示例 JSON 片段；
- 方向 1 落地后：token 过期/不匹配的报错指回 pre-complete 重取，而非通用 4xx。

旁支意见，不阻塞推进。
