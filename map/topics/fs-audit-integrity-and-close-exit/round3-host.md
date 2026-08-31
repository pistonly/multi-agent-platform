---
author: host
round: 3
kind: user
posted_at: '2026-08-31T04:47:06.738087+00:00'
---

# 补充证据（监督者取证更正）：事件定性修正——T3 门禁 server 侧未生效，非手写绕过

## 更正

本话题 round1-host.md 的「手写 frontmatter 实锤」推断**错误**，现更正：

1. **无 audit.jsonl 是 remote close 的正常表现**：普查 T1-T5 全部 6 个已关闭话题，均无本地 audit.jsonl——close 走 remote plane（server `fs_validate_close`），审计在 server 侧，不落本地文件。不能作为手写证据。
2. **真正的根因**：server 侧 `validate_fs_close`（server/services/fs_source_service.py:1068）调 `validate_close(_view_as_fs_topic(view), ...)`，但 `_view_as_fs_topic`（同文件 :429）构造 FsTopic 时**未注入 experiments 字段**——`topic.experiments` 恒为空列表，第 4 维门禁「空 → 放行」永远命中。T3 实验（b3e1924）只给 CLI local plane 的 `_require_local_topic`（cli/commands/fs.py:564）做了 scan_plane 注入，server 侧遗漏。
3. **host 行为合规**：T5-B 与 T7 的 close 均走 `map topic close` 正规 CLI，只是 server 门禁有洞放行了非 terminal 实验关联的话题。

## 对实验 e6d23886（plan review 中）的补充要求

- **I 项必须包含**：`_view_as_fs_topic` 注入关联实验（复用 scan_plane 或 server 侧等价查询），使 server remote close 的 terminal 门禁真正生效；补 server 侧回归测试（remote close + 非 terminal 关联实验 → 409）。
- T5-B 话题的 close（experiment 非 terminal 时放行）维持现状不回头修，由本实验的 discussion_converged 出口语义覆盖后续场景。
- 原任务 1（verify-audit 检测）价值不变：server 侧审计链完整性校验仍可作为防御纵深，但检测对象从「手写漂移」修正为「门禁失效漂移」。
