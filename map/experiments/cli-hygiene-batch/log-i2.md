# 207d7c4b I2 — CLI 报错与收尾动线(A2+A6)

commit `f4c0316`。I1 放宽后 log 可在 running 期直接写平台,不再走 FS 旁路。

## 实施 log

- **A2 log CLI 前置校验**(`cli/commands/experiment.py`,约 5 行):`experiment log`
  `--summary` 传了但 `--file`/`--log-file-path` 都没传时,不再让 pydantic
  `ExperimentLogCreate` 直接构造泄漏 ~30 行 ValidationError 堆栈;前置一行式
  `Error: --summary requires --file or --log-file-path` + `exit 2`,与既有
  `--file`/`--log-file-path` 互斥校验对齐。
- **A6 pre-complete 回显 complete 命令行**(`cli/commands/experiment.py`):
  pre-complete 成功输出末尾新增 `Next (copy-paste, ...)` 回显,把
  `experiment complete --id <resolved-uuid> --metadata <本次路径>
  --summary '<summary>' --log-file-path <log.md>` 拼成可粘贴单行;`--id`/`--metadata`
  按本次入参填好,`--summary`/`--log-file-path` 是 complete 侧必填、pre-complete
  未接收的参数,作显式占位(M55 recovery_command 形态)。resolved uuid 用
  nonlocal 在 `_action` 内捕获,slug/短前缀也能回显持久化 id。
- **A6 complete 缺 metadata 错误前置**(`cli/main.py::_load_complete_metadata`):
  错误信息在 accepted-key 列表之外新增示例 JSON 片段(`{"api_health": "ok",
  "pytest_summary": {...}}`)与 `--schema` 完整模板提示——「complete 还要再传
  metadata」的要求不再只在失败后才知道,收敛试探式重试。
- **A6 help 场景说明**:`experiment complete` 与 `experiment log` 两侧的
  `--file`/`--log-file-path` 各补一行使用场景(全文入平台跑全文相似度 vs
  只记路径瘦身跳过相似度)。

## 风险

- test_cli_error_envelope.py 5 挂经 `git stash` 验证为**既有债务**(stash 掉
  I2 改动后复现同 5 挂,stderr 全空),与本改动无关,未顺手修以免污染窄提交;
  后续话题如需可单独排修。
- 其余相关回归(experiment CLI plugin、experiment start/complete schema 等)
  均全绿;新装的 hygiene 4 项测试全绿。

## acceptance

| 验收 | 状态 | 证据 |
|------|------|------|
| A2 一行式报错无 pydantic 堆栈 | ✅ | 单测 test_log_summary_without_content_one_line_error(stderr 无 ValidationError、零请求发出) |
| A6 pre-complete 回显命令行 | ✅ | 单测 test_pre_complete_echoes_copy_paste_complete_command(含 --id/--metadata/--summary/--log-file-path 四段) |
| A6 complete 缺 metadata 前置 accepted keys+示例 JSON | ✅ | 单测 test_complete_missing_metadata_error_previews_keys_and_example |
| A6 help 场景说明 | ✅ | complete/log 四处 option help 各含使用场景句 |
