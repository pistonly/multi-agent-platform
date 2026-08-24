---
title: 测试债：map work 的 --client waker 标记已上线，test_map_command_client_work_requests_wakeable
  断言未同步（main 预存红）
status: closed
round: round1
creator: host
created_at: '2026-08-23T15:48:36.973441+00:00'
description: '背景：commit 97ed83d 为 map work 增加 --client waker 标记（server 区分 waker 轮询与人工调用，刷新
  last_waker_poll_at），cli/map_command_client.py work() 已带该参数。但 tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable
  的断言仍期望 [''work'',''--notification-category'',''wakeable'']，未含 ''--client'',''waker''，在
  main 上稳定失败。


  复现：.venv/bin/python3 -m pytest tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable
  -q


  期望：更新测试断言覆盖 --client waker 语义（可另加负例：人工调用不带 --client 不污染 last_waker_poll_at）。涉及文件：cli/map_command_client.py:97、tests/test_simple_waker.py:295
  附近。


  发现途径：fs stale-nudge 修复的回归测试批次中发现，与本修复无关（fs_source_service.py 改动已验证隔离）。'
participants:
- host
- participant
close_reason: no_experiment_needed
close_note: 'decision: 采纳 participant 立场——只修断言(同步 test_map_command_client_work_requests_wakeable
  到含 --client waker 完整参数列),不新增负例;rationale: participant 核证负例已有 test_waker_heartbeat_cli.test_work_passthrough_no_client(CLI
  层人工路径不带 --client)+ test_waker_heartbeat.test_plain_call_refreshes_only_api_seen(API/service
  层不刷 last_waker_poll_at)双层覆盖,再叠冗余边际防护≈0;action_items: [];修复已由 host 落实(commit a5329f0),断言测试+两负例套件共
  12 passed 全绿,ruff check 通过'
---

# 测试债：map work 的 --client waker 标记已上线，test_map_command_client_work_requests_wakeable 断言未同步（main 预存红）

背景：commit 97ed83d 为 map work 增加 --client waker 标记（server 区分 waker 轮询与人工调用，刷新 last_waker_poll_at），cli/map_command_client.py work() 已带该参数。但 tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable 的断言仍期望 ['work','--notification-category','wakeable']，未含 '--client','waker'，在 main 上稳定失败。

复现：.venv/bin/python3 -m pytest tests/test_simple_waker.py::test_map_command_client_work_requests_wakeable -q

期望：更新测试断言覆盖 --client waker 语义（可另加负例：人工调用不带 --client 不污染 last_waker_poll_at）。涉及文件：cli/map_command_client.py:97、tests/test_simple_waker.py:295 附近。

发现途径：fs stale-nudge 修复的回归测试批次中发现，与本修复无关（fs_source_service.py 改动已验证隔离）。
