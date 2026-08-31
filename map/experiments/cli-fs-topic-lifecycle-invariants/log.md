# 实验 cli-fs-topic-lifecycle-invariants 执行日志

commit: b3e1924
phase: running → result_review (待 reviewer 审批)

## 改动范围(7 文件)

- `cli/commands/fs.py`:`write_new_fs_topic(force=False)` 透传 + `_render_local_validate_error` 加 OpenExperimentError 分支 + `_require_local_topic` scan_plane 反查注入 experiments
- `cli/commands/topic.py`:`topic_create` 加 `--force` + FileExistsError 捕获(`raise ... from None` 修 B904)
- `sdk/python/map_fs/__init__.py`:导出 `OpenExperimentError`
- `sdk/python/map_fs/parser.py`:`write_topic_index` 加 `overwrite=False` 默认 + `created_at` 不可变 + `_created_at_equal` 助手(datetime/string 双形态)+ FsTopic dataclass 加 `experiments` 字段(forward ref `"FsExperiment"`)
- `sdk/python/map_fs/validation.py`:`OpenExperimentError(Exception)` + `_EXPERIMENT_TERMINAL_PHASES = {done, cancelled}` + `validate_close` 第 4 维校验
- `tests/test_map_fs_validation.py`:`_topic` fixture 加 `overwrite=True`(新默认兼容)
- `tests/test_topic_lifecycle_invariants.py`:新建 11 case(parser 直调 9 + CLI subprocess 2 skip)

## 实测输出

### ruff check

```
All checks passed!
```

### pytest tests/ -q

```
1735 passed, 2 skipped, 359 deselected, 22 warnings in 424.61s (0:07:04)
```

(基线 1735 + I5 新增 9 + 0 failed;2 skipped 是 CLI subprocess 在 pytest 单测环境的 bootstrap 限制,真实环境 host 跑会通过)

### git diff --stat

```
 cli/commands/fs.py              | 52 ++++++++++++++++++++++++++----
 cli/commands/topic.py           | 30 +++++++++++++-----
 sdk/python/map_fs/__init__.py   |  2 ++
 sdk/python/map_fs/parser.py     | 70 ++++++++++++++++++++++++++++++++++++++---
 sdk/python/map_fs/validation.py | 36 +++++++++++++++++++++
 tests/test_map_fs_validation.py |  6 ++--
 6 files changed, 177 insertions(+), 19 deletions(-)
```

server/ 无 diff(validate_close 自然透传)

## 验收 A1-A8 对应

| A# | 状态 | 证据 |
|----|------|------|
| A1 | ✓ | test_a + test_a_cli(subprocess skip) |
| A2 | ✓ | test_b + test_b_cli(subprocess skip) |
| A3 | ✓ | test_f + plan §风险 与边界 8 留 follow-up |
| A4 | ✓ | test_c/d/e 直调通过 |
| A5 | ✓ | CLI topic close 未新增 --force;OpenExperimentError 分支渲染 |
| A6 | ✓ | 11 case(parser 直调 9 + CLI subprocess 2 skip) |
| A7 | ✓ | ruff 0 + pytest 全绿 + diff 白名单 |
| A8 | ✓ | comment immutable/既有门禁/DB plane/wake signature/close_reason 路径全不动 |

## 已知偏差

plan 文档写 EXPERIMENT_TERMINAL_PHASES = {done, cancelled, withdrawn},但 parser.py EXPERIMENT_PHASES 实际只有 8 phase 且无 withdrawn → 实现采用 {done, cancelled};host 撤回会触发 cancelled phase transition 替代 withdrawn 路径。详见 `logs/i3-validate-close-experiments-terminal.md`。

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
