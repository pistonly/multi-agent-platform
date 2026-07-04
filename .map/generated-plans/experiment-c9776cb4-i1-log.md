# v0.8 实验 I1 执行日志

实验：`c9776cb4-11f6-406e-baae-de3f259ef11d`  
执行人：multi-agents-platform-host（Cursor agent，runtime-waker 路径）  
时间：2026-07-01

## 目标（I1）

删除 `cli/host_worker_topic.py` 的 `ROUND_SUMMARY_RE` 正则 fallback，改用 `topic.round_summary_count` + `discussion_round` 与 bridge 本地 `last_posted_summary_round` 驱动轮次门禁。

## 变更文件

| 文件 | 变更 |
|------|------|
| `cli/host_worker_topic.py` | 移除 `ROUND_SUMMARY_RE` 及 `_host_has_round_summary*`；新增 `_needs_round_summary_for_topic`、`_needs_advance_round_after_summary` |
| `cli/host_topic_lifecycle.py` | 轮次 Summary / advance / 开实验门禁改读 topic 字段 + bridge state |
| `cli/host_worker.py` | 更新 re-export |
| `docs/MAP-RUNTIME-WAKER.md` | 增补「v0.8 保留正则」段（C 类清单） |

## 验证

```bash
grep -n 'ROUND_SUMMARY_RE' cli/host_worker*.py
# OK: no matches

pytest tests/test_topics.py tests/test_runtime_waker.py tests/test_host_worker.py -q
# 58 passed
```

## 验收对照

- [x] `ROUND_SUMMARY_RE` 在 `cli/host_worker*.py` 无匹配
- [x] 门禁逻辑依赖 `round_summary_count` / `discussion_round`，不再扫描评论正文
- [x] `tests/test_topics.py` + `tests/test_runtime_waker.py` + `tests/test_host_worker.py` 全绿
- [ ] 端到端 bridge dogfood（plan 统一定义，留待 P3 / 实验完成前）

## 风险与后续

- **风险**：极旧话题若缺少 `discussion_round` / `round_summary_count` 字段，开实验门禁不再回退到评论正则计数；v0.7 已落字段，本仓库数据已迁移。
- **后续**：I2 runner 解析抽象（依赖 I1 测试稳定后推进）。
