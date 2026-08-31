# I1 — write_topic_index 加 overwrite + created_at 硬约束

## 改动

`/sdk/python/map_fs/parser.py:write_topic_index`

新增两个关键字参数 + 两个分支守卫：

1. **`overwrite: bool = False`** 默认参数
   - `index_path` 已存在 + `overwrite=False` → 抛 `FileExistsError("fs topic '<slug>' already exists at <path> (pass overwrite=True or use --force to replace)")`
   - 与既有 `write_round_comment`（parser.py:782）的 immutable 约定对齐

2. **`created_at: str | None = None`** 显式传参入口（仅 amend 路径使用，普通 topic create 不暴露）
   - `index_path` 已存在 + 显式 `created_at` 与旧值不同 → 抛 `ValueError("created_at is immutable, use `map topic amend --created-at` (old=<old>, attempted=<new>)")`
   - 同值传回 → 视为幂等不报错

3. **保留语义**：overwrite 路径下
   - `created_at` 不变（旧值透传）
   - `experiments` 关联列表保留（按 append 合并；本次 cli topic create 不传 experiments 字段，所以保留旧列表）
   - `participants` 已有 → 保留
   - 评论文件 `round<N>-*.md` 不动（write_topic_index 只操作 index.md）

## grep 核证

```
sdk/python/map_fs/parser.py:write_topic_index  # 715 行起
  overwrite: bool = False
  created_at: str | None = None
  if index_path.exists() and not overwrite:
    raise FileExistsError(...)
  if created_at is not None and index_path.exists():
    old_created = old_meta.get("created_at")
    if old_created is not None and str(created_at) != str(old_created):
      raise ValueError(...)
```

## 验收对应

- A1 ✓：index.md 已存在 + overwrite=False → FileExistsError 文案含 "fs topic" + "already exists"
- A2 ✓：overwrite=True 保留语义（评论 / created_at / experiments / participants 全部不动）
- A3 ✓：created_at 偷渡 → ValueError 含 "created_at is immutable"
- A8 ✓：不动 `write_round_comment`（parser.py:782）既有 FileExistsError 路径

## 已知未做

- `cli/commands/fs.py:write_new_fs_topic` 透传 `force` 参数：I2 实施
- `cli/commands/topic.py:topic_create` 加 `--force` 标志：I2 实施
- validation 层 + close 路径：I3 实施
- CLI 错误捕获：I4 实施
- 回归测试：I5 实施

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
