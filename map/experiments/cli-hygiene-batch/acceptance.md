# experiment 207d7c4b complete — experiment 域 CLI 卫生三合一

commit 链:I1 `4ef1ca2`(log 放宽)/ I2 `f4c0316`(报错与收尾动线)/ I3 `325cce5`
(路由与别名)/ I4 `fce538d`(文档)/ 事实源 `a82274a`。执行 log 见 `log-i1..i5.md`
(平台 log id 由 `experiment logs --id 207d7c4b` 的 timeline 承载)。

## acceptance(A1-A6 对照)

| 验收 | 状态 | 证据 |
|------|------|------|
| A1 log 阶段放宽 | ✅ | log_service 白名单除 cancelled 全放;单测 2(draft→review→approved 201 连续时间线 + cancelled 422);执行日志从 I1 起直接写平台(self-bootstrap) |
| A2 log CLI 一行式报错 | ✅ | `experiment log --summary` 缺内容文件 → `Error: --summary requires --file or --log-file-path`,exit 2,无 pydantic 堆栈,零请求(单测 2) |
| A3 补偿流程退役(删第 8 条后半句) | ✅ | SKILL.md:48 后半句删除、前半保留;grep log-rN/落 FS/补记 零残留 |
| A4 --topic-id slug 双路由 | ✅ | 声明放 str;非 uuid → `topic_id_for_slug` uuid5(单测:slug body==uuid5 / uuid 透传不回归) |
| A5 参数收敛 v1(--id 别名 / did-you-mean) | ✅ | fs 5 命令 `--topic`+`--id` 双轨;did-you-mean 三形态落点单测 2;三命令 help/error 核对表见 log-i5 |
| A6 收尾动线 | ✅ | pre-complete 回显完整可粘贴 complete 命令行(pre-complete 实测见本文件下方);complete 缺 metadata 错误前置 keys+示例 JSON(单测);--file/--log-file-path help 各一行场景 |
| 测试面 | ✅ | 相关回归 86 passed(I1 2 / I2 4 / I3 5 新增 + 既有回归);ruff 全改文件绿 |

## 实测输出(slim-form evidence_keys 要求)

- **pre-complete 实测回显**(A6 收尾动线真实输出,epoch 2026-08-23):

```
$ map --persona host experiment pre-complete --id 207d7c4b-... --metadata complete-metadata.yaml
experiment_id: 207d7c4b-930c-4435-b226-dead4d3286bc
phase: running
current_plan_version: 2
evidence_keys:
- acceptance
- pytest_summary

Next (copy-paste, fill in <summary> and <log.md>):
  experiment complete --id 207d7c4b-930c-4435-b226-dead4d3286bc --metadata /path/complete-metadata.yaml --summary '<summary>' --log-file-path <log.md>
```

- **--topic-id slug 创建成功**路径由单测 test_create_topic_id_slug_resolves_to_uuid5
  (payload.topic_id == `topic_id_for_slug("my-slug")`)持证;draft log 落库由 I1
  timeline 与 test_log_phase_whitelist 持证。

## 风险

- test_cli_error_envelope 5 挂为**既有债务**(stash 掉 I2 改动后复现同 5 挂,stderr
  空),与本实验无涉;did-you-mean 挂点在 subcommand_format,该层回归 40 项全绿。
- --topic-id slug 无存在性校验(FS 语义确定性映射,与 waker-heartbeat 实证一致);
  若需「幽灵 slug 拒绝」可归 backlog。
- did-you-mean 的 Missing-param 提取绑定 vendored click 消息措辞,已按实测两处
  适配(Missing parameter: <param-name> 形态 + extra argument(s) 复数),未知形态
  兜底无 hint(零误伤)。
