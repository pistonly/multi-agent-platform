# M56 结果验收（reviewer，2026-08-16）

对象：实验 c5a7aa35-da75-4e3c-b03e-a69f1d537782（plan v2 = 7c4d2990）
方式：独立复测（非采信 host 自报证据）

## 独立复测记录

| 项 | 复测结果 |
|----|----------|
| `tests/test_topic_routing.py` | 33 passed（与声明一致，含 18 个 M56 新用例） |
| fast-gate 全量 | 473 passed, 2 skipped, 1131 deselected（与声明一致） |
| `test_cancel_twice_returns_422_not_500`（slow） | 1 passed（422 terminal phase 锁定） |
| ruff check server/ cli/ tests/ | All checks passed |
| wire：reviewer persona `topic dismiss --id v012-ergonomics-review` | No-op 提示，exit 0 ✅ |
| wire：reviewer persona `topic resolve --id … --file plan.md` | exit 2 + close/close_reason 提示 ✅ |

## Acceptance 逐条核验

1. 六命令三形态路由 ✅：代码审查 + TestSixCommandDbBranch（uuid→SDK 参数断言）+ slug 分支；复用 `_resolve_topic_ref`，diff 中无新解析逻辑。
2. fs 降级二分 ✅：`_fs_transition_rejected` / `_fs_projection_noop` 单一来源模板；7 个降级用例 + reviewer 亲自实弹两形态。
3. migrate / archive DB-only 标注 ✅：archive help 补齐，migrate 原有；分组闭合守卫防未来漂移。
4. cancel 封装 ✅：`_rid` 短 id 复用；信封透传 stub 断言 + wire（已 cancelled 422 / 未知 404）；`_WRITE_COMMANDS_2` 登记经 dry-run 守卫测试强制（未登记即红，证明登记真实有效）。
5. help 一致 + 测试 + fast-gate ✅：见独立复测记录。

## 认可点

- **存量缺陷的发现与根因修复超出 plan 字面但完全正当**：双重 cancel 500 的根因（裸 StateMachineError 无 handler 映射，殃及全部直接调用 validate_phase_transition 的转换点）在 wire 实测中暴露，修复为映射表一行 + API 级回归锁，符合「验收演示即验收标准」的实验精神；日志记录完整（发现过程 / 根因 / 影响面 / 修复 / 回归锁 / wire 验证六段齐全）。
- 工程纪律：ruff format churn 回退重放（最小 diff）、临时实例 MAP_DATABASE_URL + DB 副本（吸取 M55 事故教训）、8017 实例与副本文件清理均有记录。
- 测试设计质量：`api_stub.calls == []` 反证 fs 路由零 API 调用；分组闭合守卫（`test_topic_command_groups_are_closed`）把 help 一致性从快照变成不变量。

## 非阻塞建议（移交后续，不阻塞验收）

1. `topic read` / `dismiss` 等 DB 分支在 8001 旧进程上可用但双重 cancel 422 需 server 重启后生效——建议 v0.12 收官时统一重启一次 server 并顺手验证 E8 slim create（M55 遗留等待项一并激活）。
2. `TestSixCommandDbBranch` 的 stub 返回 dict 而非 pydantic 模型——若未来 `_run` 渲染逻辑变化可能失配，届时可换 SDK 真实 Read 模型（当前无必要）。

## 结论

7 条 acceptance（plan v2 五条 + 演示脚本 + 门禁）全部达成，证据可复现。**接受实验结果。**
