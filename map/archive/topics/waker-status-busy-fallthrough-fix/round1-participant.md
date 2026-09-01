---
author: participant
round: 1
kind: user
posted_at: '2026-08-31T10:32:28.537378+00:00'
---

# round1 表态（participant 视角）

读完 host 的 T9-B 任务书（现象：T9 d12c328c 实测 smoke 失败，busy 624s + last_poll 冻结在 busy 起点 → `map waker status` 判 dead 与 server 口径相反；根因 `cli/waker_status_view.py` A2 分支 `busy_age ≤ busy_stale_w` 时无短路返回，穿透到 L242-247 gap 判定；T9 case (a) fixture `last_poll_gap_s=2.0` 恰好绕过穿透路径；任务三件套：穿透修复 + fixture 归真 + 段二 smoke 强制；边界：只动 cli+tests、不改 lib/skill、向后兼容）。整体支持——这是 T9 同源新失败模式，T9 验收跳过段二真实 smoke 是直接教训。

## §1. 整体支持 + 严重性确认

- ✅ 根因分析可信：A2 分支在 busy 未超阈值时无短路返回，busy_age 穿透为 gap 触发 dead 升级，是典型的「状态机分支缺失」bug
- ✅ 这是 P0 复发：T9 commit 7b66a38 进来后真实场景仍误报 dead，CLI 视图在最关键「waker 正忙」场景不可信——T9 任务书承诺的「修复」实际未闭环
- ✅ fixture 归真方向对：`last_poll_at = busy_started_at` 直接编码「poll 冻结」语义，比 `last_poll_gap_s=2.0` 更接近真实
- ✅ 段二 smoke 强制（task 3）支持——T9 自己的教训：I7 加给 skill 的二段式硬约束，T9 自己验收时跳过了段二，T9-B 必须强制执行，否则会重蹈覆辙
- ✅ 边界收窄到 `^cli/`、`^tests/`：lib/ 不动（fallback chain 不改）、skill 不动（I7 已落）、server 不动，与 T9-B 范围匹配
- ✅ 边界「向后兼容」：无 `busy_started_at` 的旧 state 行为不变——T9 d12c328c 加的字段，旧 state 不应被误判为 dead

## §2. 穿透修复的短路位置与 pid 优先级

任务书 task 1「pid 缺失/死亡 → dead 的优先级不变」——建议与 T9 §6 一致采用 `os.kill(pid, 0) and not is_zombie(pid)` 双重校验：

1. **短路前置**：在 `compute_waker_state` A2 分支顶部，pid 校验失败（缺失 / ESRCH / zombie）→ 直接返回 dead，跳过后续所有判定（与 T9 §6 一致）
2. **pid 存活 + busy_age ≤ busy_stale_w**：短路返回 busy（或 live，看口径）——这是修复核心
3. **pid 存活 + busy_age > busy_stale_w**：维持 stale 升级（任务书 task 1 措辞）

「死亡」在 task 1 措辞里语义模糊——是仅 ESRCH 还是含 zombie？建议 executor 落地时明确，否则 zombie pid 可能走 busy 短路，复现「defunct pid 误报 live」bug。建议测试加 zombie fixture 钉死。

## §3. fixture 归真 + 回归矩阵

建议 fixture 改造为「`last_poll_at = busy_started_at`」直接编码 poll 冻结语义（比 `last_poll_gap_s=2.0` 更直接，且不需要 freezegun 注入 current_time）。回归矩阵：

| fixture | busy_age | last_poll_at | pid | 期望状态 |
|---------|----------|--------------|-----|----------|
| T9 case (a) 修正 | 2s | busy_started_at | alive | live/busy |
| busy 穿透回归 1 | 600s (10min) | busy_started_at | alive | busy（非 dead） |
| busy 穿透回归 2 | 1860s (31min) | busy_started_at | alive | stale（边界） |
| pid zombie | 600s | busy_started_at | defunct | dead（zombie 优先级） |
| 旧 state 兼容 | n/a | 旧 last_poll | n/a | 维持原 gap 判定（无 busy_started_at → 不短路） |

共 5 条 fixture（修正 case (a) + 新增 4 条）。任务书 task 2 要求「≥2 case」是底线，建议 executor 直接落 4 条覆盖完整边界，避免下次又漏维度。

## §4. 段二 smoke 强制（task 3）支持 + 强化建议

强烈支持 task 3。T9 自己的教训摆在面前：I7 加给 `.cursor/skills/experiment-host/SKILL.md` 的二段式硬约束，T9 自己验收时跳过段二真实 smoke，commit 7b66a38 进来后真实 3-waker 环境就翻车。建议强化：

1. **log.md 结构化字段**：监督者执行段二时记录「监督者名字 + 时间戳 + 实测命令（`map waker status` 完整输出）+ `map work` 对照输出 + 同帧一致性结论」。verifier 自动核对字段存在 + 命令输出与对照输出同源，而非事后补一段散文
2. **验收 checklist 不可绕过**：本实验 log.md 缺段二记录时，reviewer 可在 result_review 阶段 reject-result（不是 host 也不是 participant 的事，留 executor / verifier 落地）
3. **段二 smoke 必须覆盖 host busy >5min**：任务书已写明；建议具体化为「host 在跑长会话（含 todo 巡检或多话题批处理）时执行」，比单纯「>5min」更可验证

## §5. 边界第 1 条「不动 lib/ 与 skill」的额外建议

T9 落地项：I3 atomic write（lib/waker_state.py）、I4 pid zombie（lib/waker_state.py）、I7 验收模板二段式（.cursor/skills/experiment-host/SKILL.md）——这些都在 lib/ 与 skill 下。T9-B 白名单不带这两个目录，等于「不能动 T9 已落地的代码」。建议 executor 在 plan.md 阶段明确：lib/ 与 skill/ 不动 → 不要顺手 refactor fallback chain、不要补 I7 镜像条款到 topic-host SKILL.md（虽然我 §6 会提议）。

## §6. 后续 followups（不阻塞 T9-B，留 host / 下次话题决策）

- **I7 二段式条款镜像到 topic-host SKILL.md**：当前 I7 只在 experiment-host skill。话题开实验时强制段二 smoke 比实验验收时强制更前置，建议下次话题同步
- **T9 §6 is_zombie 与 T9-B 短路顺序的统一定义**：T9 §6 写「defunct pid 升级 stale/dead」，T9-B 任务书 task 1 写「pid 缺失/死亡 → dead 的优先级不变」。两份文件应统一 zombie 走 dead 的判定，避免 executor 落地时摇摆
- **busy 期间 last_poll_at 冻结的现象应在 log 里可见**：本次靠「最小复现」+ 监督者实测发现。若 waker 状态视图能显示「last_poll_at frozen since busy_started_at」字段，下次类似 bug 可由运维先看见而非事后排查

## §7. 立场

议题清晰、修复方向对路、范围收窄合理，同意 host 推进开实验。T9-B = `waker-status-busy-fallthrough-fix`，任务 I1-I3（穿透短路 + fixture 归真 + 段二 smoke 强制），验收 ≥ 5 case（修正 case (a) + 4 新增），白名单 `^cli/`、`^tests/` 风险面可控。不阻塞 host 推进。
