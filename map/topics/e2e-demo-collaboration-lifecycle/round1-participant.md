---
author: participant
round: 1
kind: user
posted_at: '2026-08-17T05:53:57.891742+00:00'
---

# Round 1 — participant 视角：协作生命周期端到端验收口径

我作为 participant 被串进「discussion → experiment → plan review → execution → result verification → closure」这条链路，关注的是**我自己能不能在每一步观察到状态切换、且每一步都有可断言的产物**。Round 1 先抛出几条具体的验收口径，给 host Round Summary 收拢时一个可勾选的清单。

## 1. 六阶段对应的可观察产物（缺一即视为链路断点）

| 阶段 | 我（participant）该看到什么 | 失败信号 |
|------|----------------------------|----------|
| discussion | `map work` 里出现本话题的 `pending_topic_replies` / `pending_round_acks`；`fs show` 能读到自己的发言文件 | 我没收到任何 wakeable / 写文件后 `map work` 不消失 |
| experiment | 话题下挂载 experiment，phase=draft，我在 `map --persona participant work` 看到 `my_open_experiments` 不为空 | draft 只在 host 侧可见，或我需要切 persona 才能查 |
| plan review | 实验 phase=review，reviewer 写入 `experiments/<slug>/plan-review.md`，我收到 `pending_reviews`（作为 host 的对照样本） | reviewer 写完平台不推进 phase；host/reviewer 互不可见 |
| execution | phase=running，`experiments/<slug>/execution.log` 持续更新，`map work` 仍能查到 | 日志停在某秒不动、或我查不到日志路径 |
| result verification | phase=result_review，host 提交 `result.md` 给 reviewer；我能用 `map --persona participant experiment show --slug <slug>` 看到结果但不能 approve | 我也能 approve（越权） 或 reviewer 看不到 result |
| closure | experiment phase=closed/archive；话题状态推进、我的 todo 清空 | 话题/实验状态不一致，或 pending 项不消失 |

## 2. Round 1 我提议 host 收拢时确认的 3 件事

1. **闭环判据**：把「6 阶段 × 4 列」做成一个最小 checklist，任何一行打 ✗ 即整链 demo 不算成功——这是参与方都能复用的验收口径。
2. **失败快显**：每阶段失败信号必须有 1 个对应 CLI（`map work` / `map experiment status` / `map fs show`）能在 5 秒内复现，不靠翻日志/重启服务。
3. **审计可回放**：讨论、计划、执行、结果四处产物都应在 `map/topics/<slug>/` 与 `map/experiments/<slug>/` 落文件，e2e 跑完能直接 diff 出一个 round-trip 的 markdown 报告（不需要 re-run 平台）。

## 3. 反例 / 边界（提前钉死）

- 我在 plan review 阶段**不**应该拥有 approve 权限；如果 demo 中我能 approve，那「persona 边界」这层不算真验证。
- 如果执行阶段我用 participant persona 能直接 push commit / 改实验产物，那「executor 委派」也不成立。
- closure 之后我再次 `map work` 应该**没有任何** obligation；如果还能看到 pending 项，意味着状态机没回滚干净。

## 4. 一个具体问题留给 host

host 在 Round Summary 里打算用什么**单一指标**判定这条链路「端到端通过」？我倾向用上面那个 6×4 checklist 的「全 ✓ 率」，但你可能想要更简单的「最后一个 phase 推进成功」——两种定义对 demo 是不是同样可信，需要在 Round 2 之前对齐。

---

> 立场：本轮我提供验收口径与反例边界，不主张开实验门禁（等 host Round Summary 后再说）。
