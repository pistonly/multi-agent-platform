# 207d7c4b I3 — 路由与别名(A4+A5)

commit `325cce5`。

## 实施 log

- **A4 --topic-id 双路由**(`cli/commands/experiment.py::experiment_create`):
  `topic_id` 参数声明从 `uuid.UUID | None` 放宽为 `str | None`;非 uuid 形态视为
  FS 话题 slug → `topic_id_for_slug(slug)`(确定性 uuid5,本地纯函数,零请求),
  无需再先 `topic show` 抄 uuid;DB 话题 uuid 直传路径保持原样。
- **A5 fs 域 --id 双轨别名**(`cli/commands/fs.py`):5 个 `--topic` 命令
  (comment/show/advance-round/close/archive)注册 `--id` 别名,`--topic`/`--id`
  double option name 绑定同一参数——过渡期双轨不 break,退役留 major 版本窗口。
- **A5 did-you-mean**(`cli/subcommand_format.py::_patch_did_you_mean`):
  在既有 `_patch_leaf` 上对 id/topic 域命令 patch `parse_args`,把 click
  UsageError 的消息按四种形态附加一行 `Hint:`(不改 exit code):
  1. missing option(vendored click 报 `Missing parameter: <param-name>`,
     经 param→flags 反查命中 `--id`/`--topic`→「你是不是想用 --id <slug>」
     验收字面形态);
  2. extra argument(s)(已给 id 又裸传 slug → 建议 `--id <token>`);
  3. no such option(拼错 flag → difflib 近邻匹配已知 option);
  4. 兜底:非 id 域缺失/不可匹配时不附加 hint(零误伤)。
  message 与 args[0] 双通道写回,保证任一渲染路径带 hint。

## 风险

- did-you-mean 只作用于含 `--id`/`--topic` option 的命令;`Missing parameter`
  提取依赖 vendored click 的 message 措辞——已按实测两点适配
  (`Missing parameter: experiment_id` 形态 + `extra argument(s)` 复数形态),
  并对未知措辞兜底为无 hint。
- `--topic-id` slug 无存在性校验:乱 slug 也会生成幽灵 uuid5(FS 语义本就是
  确定性映射,与 waker-heartbeat 实证路径一致);plan 只要求「直接成功」。

## acceptance

| 验收 | 状态 | 证据 |
|------|------|------|
| A4 slug 双路由直接成功 | ✅ | 单测 test_create_topic_id_slug_resolves_to_uuid5(body.topic_id == uuid5) |
| A4 DB uuid 直传不回归 | ✅ | 单测 test_create_topic_id_uuid_passthrough(body.topic_id 原样透传) |
| A5 --id 别名 + --topic 双轨 | ✅ | 单测 test_fs_comment_id_alias_registered(help 显示 `--topic, --id`)+ 5 命令声明核对 |
| A5 did-you-mean(missing option) | ✅ | 单测 test_extra_argument_did_you_mean(裸传 slug→「你是不是想用 --id <slug>」,零请求) |
| A5 did-you-mean(no such option) | ✅ | 单测 test_no_such_option_did_you_mean(`--sumary`→「你是不是想用 --summary」,零请求) |
| 回归 | ✅ | subcommand_format/required-guard/shortid/fs-archive 40 项 + 新装 5 项全绿;ruff 通过 |
