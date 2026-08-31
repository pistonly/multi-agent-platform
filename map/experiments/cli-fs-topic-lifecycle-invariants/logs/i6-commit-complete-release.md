# I6 — commit + complete + release

## commit b3e1924

```
map exp 7aeabc2e: cli/fs topic create + close terminal guards (I1-I5)

- write_topic_index 默认拒绝 overwrite,--force 显式覆盖保留 created_at
  + 评论;created_at 改值由 parser 层 ValueError 拦截(独立 amend 路径)
- FsTopic 增加 experiments 字段(CLI scan_plane 反查注入),
  validate_close 新增第 4 维校验:关联实验必须达 terminal(done/cancelled)
- CLI topic create 加 --force 标志 + FileExistsError 捕获;close 失败
  携带 OpenExperimentError 携带的 non-terminal 实验 id + phase 渲染
- regression: 11 个 case 双层(parser 直调 9 + CLI subprocess 2 skip)
- 1735 passed, 2 skipped, 0 failed(全量回归)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

## narrow whitelist 核验

```
$ git diff --stat
 cli/commands/fs.py              | 52 ++++++++++++++++++++++++++----
 cli/commands/topic.py           | 30 +++++++++++++-----
 sdk/python/map_fs/__init__.py   |  2 ++
 sdk/python/map_fs/parser.py     | 70 ++++++++++++++++++++++++++++++++++++++---
 sdk/python/map_fs/validation.py | 36 +++++++++++++++++++++
 tests/test_map_fs_validation.py |  6 ++--
```

全部在白名单 `^cli/`、`^sdk/python/map_fs/`、`^tests/` 内。`server/` 无 diff(validate_close
experiments 维度通过 server 调 validate_close 自然透传到 409,无需 server 侧 schema 改动)。

## ruff check + pytest 全绿

- ruff check:All checks passed!(0 error)
- pytest tests/ -q:1735 passed, 2 skipped, 0 failed

## 收尾动作(本步骤进行)

1. narrow commit 已落地 ✓
2. I1-I5 log 已写 ✓
3. I6 log(本文件)✓
4. complete-metadata.yaml 写入
5. `experiment complete` 提交结果待 reviewer 审批
6. `experiment lock release` 释放

## 验收对应

- A7 ✓: ruff + pytest + git diff 白名单三检通过
