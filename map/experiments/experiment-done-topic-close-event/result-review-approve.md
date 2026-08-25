# experiment-done-topic-close-event（f49de698）— 结果审批（accept）

## 结论

**通过**。对照 plan v2（B5 已补 topic_id 为空跳过）验收 B1-B6 + 测试面均由 reviewer 独立复跑覆盖。I3 live 与本 `accept-result` 同事务自举——审批后立即核 host `map work` 是否出现 `topic.close_pending`；若未出现再开返工实验，不在本 verdict 预设失败。

## 独立核证矩阵（reviewer 现跑，非转述日志）

| 维度 | 判定 | reviewer 独立证据 |
|------|------|------|
| B1 白名单 + 接线 | ✅ | `notification_service.py:48` 含 `topic.close_pending`；`phase_service.accept_result` 尾部调用 `_notify_topic_close_pending`；单测 accept 路径断言 recipient/文案含 slug 与 close 门禁衔接词 |
| B2 reject 不触发 | ✅ | `tests/test_topic_close_pending_event.py` reject 用例：通知表无 `topic.close_pending` |
| B3 executor 分离文案 | ✅ | 同文件 executor 分离/同人两分支断言 |
| B4 不新增 kind | ✅ | `rg topic_close_pending\|topic.close_pending` 于 `work_kinds.py` 与 `wake.md` **零命中** |
| B5 FS / DB / 空 topic_id | ✅ | 三分支单测（FS 反解、DB 降级、topic_id 空跳过） |
| B6 同事务 | ✅ | spy 捕获 `emit_kind(..., commit=False)` |
| 测试面 | ✅ | reviewer 现跑 `pytest tests/test_topic_close_pending_event.py` → **7 passed / 0 failed**（0.25s） |
| ruff | ✅ | 改动文件 All checks passed（host 落档；本轮未整仓复跑，范围限于本实验三文件） |
| 收口 commit | ✅ | `4f12eae` 实现 + `87b4577` 去掉误 return，均 `map exp f49de698` 窄提交 |
| server 已加载新代码 | ✅ | daemon pid 2585620 @ 12:48，`/health` ok；旧 pid 2233333（07:07，I1 前提交）已替换 |

## I3 时序说明（非阻塞）

plan evidence_keys 要求 `map work` 出现 `topic.close_pending`。complete 时点无法用本实验自身 accept 取证。代码路径已由七组单测钉死；本 accept 即 live 探针。审批后 host 应立刻核通知并写入 `log.md` I3 节。

## 遗留（非阻塞）

- slim `--log-file-path` 导致 complete 侧 template 四段 WARN（server 未读文件正文）；文件本身已含 summary / 实施 log / 风险 / acceptance。
- host waker heartbeat stale——与本实验无关。
