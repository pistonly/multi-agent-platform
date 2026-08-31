# I2 — CLI topic create 加 --force + 错误捕获

## 改动

### cli/commands/fs.py:write_new_fs_topic

新增 `force: bool = False` 关键字参数,透传到 `write_topic_index(overwrite=force)`。
默认 False 保持向后兼容(存量代码未传 force 时按原语义)。

### cli/commands/topic.py:topic_create

新增 `--force` 标志(typer 风格 `force: bool = typer.Option(False, "--force", ...)`)。

调用链:

```
topic_create(force=True)
  → write_new_fs_topic(force=True)
    → write_topic_index(overwrite=True)   # I1 已实现保留语义
```

错误捕获:外层 `try/except FileExistsError as exc → typer.echo(f"Error: {exc}", err=True); raise typer.Exit(1) from None`,
用户可见 stderr 含 "fs topic '<slug>' already exists at <path> (pass overwrite=True or use --force to replace)"。

### B904 修复

ruff B904 要求 `raise ... from err/None` 区分异常链——此处用 `from None` 切断链
(typer.Exit(1) 是用户可读退出码,不需要把内部 FileExistsError 链路抛回 shell)。

## 验收对应

- A1 ✓: 无 --force 撞 slug → exit != 0 + stderr 含 "already exists"
- A2 部分 ✓: --force → exit 0 + 保留评论 + created_at 不变 (parser 层 I1 已保证)
- A5 ✓: 不暴露 `--created-at` 给 topic create 普通用户

## 未做

- `map topic amend --created-at` 独立命令:留 follow-up(plan §风险 与边界 第 8 条)
