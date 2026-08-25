---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# 测试债:主干 26 个预存红放大一切验收成本 + 实验 complete 的测试证据无人校验(host 发起)

## 原始问题(2026-08-24 merge 6aaca4c 验收实录)

**1. 主干 26 个预存红**——为证明 merge 零新增失败,验收者被迫建临时 worktree 在 pre-merge commit 上逐批对照跑(三批 /tmp/pb*.log),**这笔对照考古每次验收都要付**。失败清单:
- tests/test_cli_error_envelope.py ×5(f4c0316 message 自认「既有债务」)
- tests/test_cli_format_priority.py ×3、tests/test_cli_json_schema.py ×1、tests/test_cli_n2_hard_cutover.py ×1(CLI format/json 输出族,同根因:输出在 JSON 后多段内容,JSONDecodeError Extra data)
- tests/test_error_codes_cli.py ×5、tests/test_error_envelope_and_file_options.py ×5(同族)
- tests/test_experiment_lock_notifications.py ×1(test_stalled_lock_scan_endpoint_host_scopes_to_own_project)
- tests/test_map_sdk_skeleton.py ×2(evidence.py 注释含 "from server" 字样被 grep 误报;_map_sdk_version 已不在 cli.main)
- tests/test_reject_result_misuse.py ×1、tests/test_review_list_archived_filter.py ×2

讽刺背景:fast-gate 白名单反转实验(eb291c4b)刚 done,这 26 红仍在——fast-gate 覆盖范围与修复动线有缝隙。

**2. 实验 complete 的 pytest_summary 是自报的**——f4c0316 一边自认「test_cli_error_envelope 5 挂」一边实验照常 done。「测试全绿」是四门验收的暗门禁,但 evidence metadata 无机器校验(207d7c4b A 系列建了结构,没建校验)。

## 期望

1. 清零 26 红(按族归因:CLI format/envelope 族一个根因,map_sdk_skeleton 误报一族,零散三组)
2. complete/reviewer accept 路径对 evidence pytest_summary 加校验:failed>0 或 total 与 CI 不符 → 拒绝/警告
3. fast-gate 范围核对:这 26 个为何不在 fast-gate 兜底范围,修动线还是修白名单

## 证据坐标

- 失败明细:/tmp/pb1.log /tmp/pb2a.log /tmp/pb2b.log(2026-08-24 03:1x 三批全量,session 内留存;复现:.venv/bin/python3 -m pytest <上述文件列表> -q)
- f4c0316 commit message 自认段;evidence 结构:sdk/python/map_types/schemas/experiment.py(evidence_metadata)
