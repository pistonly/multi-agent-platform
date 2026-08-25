# 结果审批：M1 实验生命周期 FS 事实源——index.md 契约 + 验证型写闭环

实验：34840a7a-02d0-4def-835d-022396154bf2（experiment-lifecycle-fs-m1）
审批人：multi-agents-platform-reviewer
决策：**accept-result**（实验进入 done）

## 验收对照（plan acceptance A1–A6）

| 项 | 验收 | 证据 | 结论 |
|----|------|------|------|
| A1 | index.md 契约：frontmatter 含 phase / current_plan_version / creator / executor / topic / updated_at；评审条目不进 index，走 reviews/*.yaml | 当前 index.md frontmatter 齐全（phase=result_review / current_plan_version=1 / creator=host / executor=host / topic / updated_at=complete 时刻）；reviews/ 独立成目录，index.md 不含任何 review 条目 | ✅ |
| A2 | 绕过 CLI 手改 phase 被 validate 拒 | log.md 记录实测报错 `experiment index.md phase is 'done', expected 'running' (hand-edited phase is rejected...)`；报错原文出自 sdk/python/map_fs/parser.py:907，代码/日志一致；tests/test_fs_experiment_index.py 覆盖 | ✅ |
| A3 | DB 行是投影；退役条件写在 index.md | index.md description 明示「DB 行仍为投影（锁/通知/review item/token）。退役条件：M2 map experiment sync --check 对账零 diff 后再停 INSERT」；show/list 已以 index.md 为准（I3） | ✅ |
| A4 | complete 全链路可 diff：git 可见 phase 变更 + reviews/ 落盘 | commit 63e40aa diff：index.md phase `running → result_review`、updated_at 更新、新增 reviews/complete.yaml（event: complete），三文件均在收口提交内 | ✅ |
| A5 | 锁仍走 DB，幂等不退化 | log I4「锁仍走 DB experiment lock；index 写不碰 lock 文件」；index.md 声明锁/通知/review item/token 在 DB；status JSON 锁字段（lock_holder_experiment_id 等）来自 DB | ✅ |
| A6 | id 三态路由：slug / uuid5 / DB uuid | log 实测 `show --id experiment-lifecycle-fs-m1` 与 uuid5 读到同一投影 id `34840a7a-...`；tests/test_experiment_id_routing.py 覆盖 | ✅ |

独立复核：复跑 `tests/test_fs_experiment_index.py + tests/test_experiment_id_routing.py` → 10 passed（对照 commit HEAD 而非工作区；工作区仅 topic 目录未跟踪，与实验交付无关）。

## 已知非阻塞项

- slim `--log-file-path` 使 complete 出现 `MISSING_TEMPLATE_SECTION` 软警告：四段（实施/风险/acceptance/收尾备注）均在磁盘 log.md 中完整，不影响审计。不驳回。
- 首次 complete 403 后 executor 由误委派的 participant 改回 host：log.md「收尾备注」有完整记录；且 A7 已由用户当场更改为 host 自执行（index.md executor=host），不以「未 --executor participant」驳回。

## 结论

A1–A6 全部满足，证据落在 committed HEAD，测试独立复跑通过。批准进入 done。
