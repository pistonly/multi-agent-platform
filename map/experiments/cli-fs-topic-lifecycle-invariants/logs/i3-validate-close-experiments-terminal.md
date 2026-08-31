# I3 — validate_close experiments terminal 校验

## 改动

### sdk/python/map_fs/validation.py

1. **新增 `OpenExperimentError(Exception)` 类**:
   - 携带 `experiments: list[FsExperiment]` 属性(仅含 non-terminal 实验)
   - `__str__` 返回 `"experiment non-terminal: <id> (phase=<phase>)[, ...]"`

2. **`_EXPERIMENT_TERMINAL_PHASES: frozenset[str] = frozenset({"done", "cancelled"})`**:
   - **plan 文档与实际状态机存在 1 处分歧**:plan 原文为 `{done, cancelled, withdrawn}`,但
     `EXPERIMENT_PHASES`(parser.py) = `{draft, review, approved, running, pending_review, result_review, done, cancelled}`,
     **没有 `withdrawn`**。FS 侧状态机只有 8 个 phase,`withdrawn` 是 DB plane 历史遗留。
   - 决策:采用 2 档 terminal(`done/cancelled`),与当前状态机对齐。`withdrawn` 走 `cancelled` 替代路径
     (host 主动撤回会触发 `cancelled` phase transition)。需要更新记忆避免后续 plan 重复踩坑。

3. **`validate_close` 第 4 维度校验**(在 action-items 检查之后、status 写入之前):
   ```python
   non_terminal = [
       exp for exp in topic.experiments
       if exp.phase not in _EXPERIMENT_TERMINAL_PHASES
   ]
   if non_terminal:
       raise OpenExperimentError(non_terminal)
   ```
   - 无 `experiments` 属性 / 空列表 → 放行(对老 fixture 友好)
   - 全部 terminal → 放行

### sdk/python/map_fs/__init__.py

新增 `OpenExperimentError` 导入与 `__all__` 列表登记。

## FsTopic 注入路径

`FsTopic` 是 `parser.py` dataclass,新增字段:
```python
experiments: list["FsExperiment"] = field(default_factory=list)
```
forward ref `"FsExperiment"` 在模块内有效。

CLI `scan_plane()` 路径:在 `_require_local_topic`(cli/commands/fs.py)中除了解析 topic,
还调 `scan_plane(workspace, root)` 反查所有 experiment,按 `exp.topic == slug` 过滤注入到
`topic.experiments`。这样 validate_close 调用时 `topic.experiments` 已有数据。

## 验收对应

- A4 ✓: 关联实验存在 + 任一 non-terminal → OpenExperimentError
- A4 ✓: 无关联实验 / 全部 terminal → 放行
- A8 ✓: 不动 owner / status / action-items 三类既有门禁(在它们之后才校验 experiments)

## 风险与边界

- **plan 文档与状态机漂移**:`withdrawn` 是 plan 阶段假设的 phase,但 `EXPERIMENT_PHASES` 未含。
  后续 plan 评审时 reviewer 应核对 `parser.EXPERIMENT_PHASES` 全集,而不是凭印象补 phase 名。
