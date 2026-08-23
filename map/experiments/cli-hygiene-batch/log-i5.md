# 207d7c4b I5 — 验证收尾 + complete

本步无新代码改动;核对表 + 全量回归 + 事实源提交后 complete。

## help/error path 三命令核对表(A5 验收 T2-P2,实测抓拍)

| 命令 | help 具名 | error path(裸传位置 slug) | 一致性 |
|------|-----------|---------------------------|--------|
| experiment create | `--topic-id TEXT  Topic UUID (DB) or FS topic slug (resolved to its deterministic uuid5, T2-P1)` | 无位置 id 场景;缺计划/互斥走一行式前置校验 | ✅ create 用 --topic-id 独立名,与 commands.md 示例对齐 |
| experiment status | `--id TEXT  Experiment UUID or >=8-hex-digit prefix (v0.12 M54B)` | `Missing option '--id'.   Hint: 你是不是想用 --id <slug>` | ✅ --id 具名 + did-you-mean |
| topic comment | `--id TEXT  Topic UUID (DB), FS uuid5 id, or slug.` | `Missing option '--id'.   Hint: 你是不是想用 --id <slug>` | ✅ --id 具名 + did-you-mean |
| fs comment | `--topic, --id TEXT  话题 slug(--id 为别名,T2-P2)` | `Missing option '--topic' / '--id'.   Hint: 你是不是想用 --id <slug>` | ✅ 双轨别名 + did-you-mean |

结论:三个高频命令在 help 与 error path 两侧具名自洽,与 commands.md 示例语法一致;
位置参数误用均落到 did-you-mean 提示,不再裸堆 click stack。

## 回归与静态检查

- I1–I4 相关全量:`test_experiment_cli_hygiene / cli_param_routing_aliases /
  log_phase_whitelist / m57_log_slim_form / cli_subcommand_format /
  required_option_guard / cli_shortid / fs_archive_command /
  cli_schema_discovery / accept_result_verdict` = **86 passed, 9 deselected**
  (deselected 为环境门控;test_cli_error_envelope 5 挂为既有债务,stash 已证与
  本实验无关)。
- `ruff check` 全改文件(cli ×4 + 新测试 ×2 + I1 测试)通过。

## 收尾提交

本实验事实源 `map/experiments/cli-hygiene-batch/`(plan / review / log-i1..i5)
一次提交,complete metadata 见 acceptance.md 路径。

## acceptance

| 验收 | 状态 | 证据 |
|------|------|------|
| 新增单测全绿 | ✅ | 上述 86 passed(I1 2 / I2 4 / I3 5 / 既有回归) |
| ruff check 通过 | ✅ | 全改文件绿 |
| help/error path 核对表 | ✅ | 上表四命令逐条 |
| 事实源归档 | ✅ | cli-hygiene-batch 目录含 plan/review/log-i1..i5 |
