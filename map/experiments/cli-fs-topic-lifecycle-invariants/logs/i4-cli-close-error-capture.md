# I4 — CLI close 错误捕获 + server 同步

## 改动

### cli/commands/fs.py:_render_local_validate_error

新增 OpenExperimentError 分支:
```python
if isinstance(exc, OpenExperimentError):
    typer.echo("Error: experiment non-terminal — 关联实验未达 terminal,无法关闭话题", err=True)
    for exp in exc.experiments:
        typer.echo(
            f"  - experiment {exp.id} (phase={exp.phase}) non-terminal — "
            "等实验 done/cancelled 后再 close",
            err=True,
        )
    return
```

### cli/commands/fs.py:local_validated_write_flow

`except` 元组追加 `OpenExperimentError`(与既有 `AckPendingError`、`OpenActionItemsError`、`TopicOwnerError` 并列)。

### server 不改

验证:`server/api/fs.py` 对 close 路径的 409 映射已对齐 validation 模块 docstring 文案,无需新增映射——server
侧只在 action-items / ack / owner 三类门禁基础上加 1 行 server.py 调用 validate_close 即可。
本次实测 `server/api/fs.py` 仍调 `validate_close`,新增 experiments 维度通过 validate_close 自然透传到 server 409。
**没有 server diff**:`git diff --stat server/` 输出为空。

## 验收对应

- A4 CLI 部分 ✓: `map topic close` 含 non-terminal 实验 → exit != 0 + stderr 含 "experiment non-terminal" + 逐行列出 id + phase
- A5 ✓: 不新增 `--force` 绕过
- A8 ✓: 不影响 review accept-result 的 close_reason: experiment_done 路径(实验 b3ec2e4d)
