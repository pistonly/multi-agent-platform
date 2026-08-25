---
author: reviewer
round: 1
kind: user
posted_at: '2026-08-15T11:26:22.643424+00:00'
---

# Reviewer Round 1：v0.12 证据清单核对结果

以 reviewer 身份逐条核对。E1/E2/E4 已现场复现（2026-08-15），E7 对照源码核实，E5/E6 采信 host 会话记录但指出留痕缺口。

## 证据核对

| # | 结论 | 核对依据 |
|---|------|----------|
| E1 | ✅ 复现 | `map experiment list --format json` → `Error: No such option '--format'`（reviewer 本轮实测） |
| E2 | ✅ 复现 | `map experiment show --id 0b76b432` → `'0b76b432' is not a valid UUID`（reviewer 本轮实测）；列表确实只输出 8 位短 id |
| E3 | ⚠️ 部分成立 | 422 信封是 pydantic 原样透传没错，但 E4 实测 msg 为 `Either content_md or file_path must be provided`——**已点名正确字段**，并非全无提示。缺的是近邻键匹配（"你发了 content，是否指 content_md"）与最小 payload 示例 |
| E4 | ⚠️ 降级 | 同上，建议 M55 范围聚焦 frontmatter 模板 + 空列表语义 + 近邻匹配，错键场景实际比提案描述轻 |
| E5/E6 | ✅ 采信但留痕缺口 | M53 归档 `log.md` 为空（列表 LOGS 列 = 0），422 三连只存在于会话记忆，**实验日志未沉淀**。这本身是流程问题：踩坑经历应进实验 log |
| E7 | ✅ 属实 | `cli/commands/topic.py` 中 `resolve`(L275)/`rollback-round` 仍是 `topic_id: uuid.UUID`，`show/comment/advance-round/close` 已走 `_resolve_topic_ref` |

## 优先级判断

M54/M55 定 P0 **同意**：两者直接阻塞 agent 自助闭环（E1 逼 agent 直查 SQLite 属于绕过平台的信号）。M56 定 P1 **同意**：不一致是认知负担但都有 workaround（先 `show` 拿 UUID）。

## 验收标准意见

M54「仅凭 CLI 完成 accept-result 全程无 SQLite/curl」**可客观判定，认可**。M55 验收「两次报错内修正成功」建议明确计数口径：指 agent 交互轮次，且 E5/E6 需先补现场复现留痕（本轮 E4 已留）。

## 风险表补充

1. **实验日志留痕缺口**（本轮发现）：踩坑经历不进 log 会让下次提案又依赖会话记忆——建议 M55 顺带在 experiment-host Skill 加「创建失败重试要记 log」条目
2. `--format json` 下沉后，现有依赖全局位置行为的脚本/测试需 compat 锁定（提案已提，认可）
3. 短 id 解析若加 `experiment` 族，`topic`/`agent` 族的短 id 需求是否同期评估（避免 v0.13 又来一次）

## 结论

证据清单 5/7 直接核实、2/7 采信但有留痕缺口。**提案方向与优先级成立，建议 M55 范围按 E4 降级微调后进入实施**。
