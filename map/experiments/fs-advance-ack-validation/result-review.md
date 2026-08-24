# advance-round ack 合规校验 v1 — 结果审批（accept）

## 结论

**通过**。D1–D4 与 A1–A6（A6 裁决 backlog 符合 plan 可选声明）逐条满足；收口 commit `4e45f4e` 在 HEAD 且窄提交（7 文件全相关）；v2/v3 评审针对 fa3b838b / 911fdb0e 的整改点全部落实到 HEAD 实现与单测。无阻塞项。

## 验收逐条核验

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 手写旁路被拒（双界面同源） | ✅ | HEAD `parser.py:128` `FsTopic.authors_in_round` 只统计 `ack_valid`；`_ack_error_of` 严格基于 RAW front-matter（显式注释防 fallback 绕过——fa3b838b 关键陷阱）；server `fs_source_service.py:292` `_TopicView.authors_in_round` 同口径过滤。单测 `test_work_and_advance_same_origin_reject_handwritten`（HEAD tests/test_fs_source.py:769）：同一 hand-write 文件在 `derive_work` round_ack_pending detail 与 advance-round 409 missing_reasons 中一致被拒，均含文件名 + `frontmatter author missing` |
| A2 空壳/伪造被拒 | ✅ | `_ack_error_of`（parser 278-301）按 D2 三条：author missing / author 与文件名 persona 不符、round missing / 与文件名轮次不符、posted_at missing/unparseable。单测 `test_frontmatter_field_semantics_ack_reasons` 逐条断言（author=host expected participant / round=1 expected 2 / posted_at missing） |
| A3 名单外不计入 | ✅ | `ack_participants()` = creator ∪ declared（动态 speaker 不扩员，D3）；名单外 reviewer 合规文件进 `stray_files_in_round` anomaly、不阻塞 advance。单测 `test_ack_participant_scope_and_stray_report`：stray == [reviewer]，host round_ack_pending 不含 |
| A4 正常路径不受扰 + 不追溯 | ✅ | CLI `write_round_comment` 合规文件 `ack_valid=True`（test_frontmatter_field_semantics 713 行 round1-participant 断言 True）；advance 正常推进（test_fs_advance_round_ack_validation: round1 双发 → round2 200）。fast-gate 手写旁路文件 `map/topics/fast-gate-allowlist-inversion/round1-participant.md` 仍存在、commit 未触碰 |
| A5 CLI 错误信息带文件名+原因 | ✅ | server `_view_missing_reasons`（1433-1443）输出 `文件名: 原因`；`FsAckPendingError(missing, reasons)`（1482）；CLI `_render_ack_error`（fs.py:566-573）409 round_ack_pending 逐行渲染 `- persona: 文件名: 原因`，缺文件 fallback「缺文件（未发言）」。单测断言 missing_reasons 含文件名字样 |
| A6 preflight（可选） | ⏭️ | 裁决 backlog，plan 显式标注可选不阻塞主验收，符合 |
| 测试面 | ✅ | **实测复跑**：tests/test_fs_source.py **28 passed**（27.9s）；tests/cli/test_fs_persona.py **5 passed**；waker + fs projection/remote/archive **152 passed**（`-k waker or projection or remote or archive`，含 59 专项）。2 failed 均为 `test_review_list_archived_filter`（引用 `cli.main.review_list` 不存在命令）——该测试与 cli/main.py 均非本实验 commit 触碰（git show --name-only 4e45f4e 无），062c60e 引入且先于 4e45f4e（merge-base 验证），**预存债务与本实验零相关** |
| ruff | ✅ | host log 声称 clean；改动面 7 文件 |

## 关键核验点

1. **911fdb0e（v2 评审 unreasonable 项）——server 同源完全落地**：HEAD `_CommentView.ack_valid/ack_error`（service:266）、`_view_from_fs_topic`（303-341）与 `_view_from_projection`（343-386）**两处映射均携带合规字段**（远程/本地视图不分叉，v3 风险段承诺）、`_TopicView.authors_in_round` 只统计合规 author（287-292）、`validate_fs_advance_round`（1448-1484）`authors = view.authors_in_round(...)` + missing → `FsAckPendingError(missing, _view_missing_reasons(...))`。单测走增强后的 `test_work_and_advance_same_origin_reject_handwritten` 直测 409 round_ack_pending 含该 persona——杜绝「parser 改了、server 没设」。
2. **fa3b838b（v1 评审项）——工作/advance 双界面同源**：`derive_work`（parser 630-687）missing 范围 `ack_participants()`、判定 `authors_in_round`（effective）——与 `validate_fs_advance_round` 完全同口径，单测 `test_work_and_advance_same_origin_reject_handwritten` 直验。
3. **commit 收口**：`4e45f4e` 为 HEAD 且窄提交，gate review 引用、单测、CLI 改动全落在这一 commit，无散落 D 遗留或事后补提交。
4. **判定严谨性**：`_ack_error_of` 特意不用解析层 fallback 值（290-298 fallback 会令手写文件「看起来合规」），与 v3 plan「保留 raw author 用于展示、合规走独立字段」的拆分一致——设计正确无旁路。

## 遗留（非阻塞）

- A6 `map fs ack-status` preflight 裁决 backlog（plan 可选，不阻塞）。
- `test_review_list_archived_filter` 2 挂为预存债务（引用不存在的 `cli.main.review_list`），与本实验无关，可归独立修复。
- plan 调研坐标 `validate_fs_advance_round` 引用 1427-1431、实际定义 1448——行号随版本漂移，语义描述准确，不构成实现偏离。
