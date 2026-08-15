---
author: host
round: 2
kind: user
is_round_summary: true
posted_at: '2026-08-15T02:07:37.189063+00:00'
---

# Round 2 — Host 回应与决策（Round Summary）

participant 的 round1 分析质量高：五个争议点均有明确倾向且核实了代码事实（特别是争议点 3 对 `fs_work_items` 无差别调用 `derive_work` 的核实）。本轮逐项决策，作为 Round Summary。

## 决策

### 争议点 1：双路径并存 → **采纳：deprecated + 1-2 个 minor 版本过渡**

- DB 话题写路径（`topic comment --body/--file`）下版本加 deprecation warning，指向 `fs comment`
- DB 话题转只读展示；过渡期满后移除写路径
- 存量 DB 话题用 `migrate-from-docs` 同款机制并入 `map/`
- 反对意见留窗口：若过渡期内出现必须 DB 评论的场景（如 webhook 外部系统回写），在话题下补发言推翻本条

### 争议点 2：实时全量扫描 → **采纳：记基线 + 定阈值，YAGNI**

- 用 perf-baselines 机制记录 `scan_plane` 当前耗时与话题数
- 在 `scan_plane` 代码处留触发条件注释：话题 >500 或单次 scan >100ms 再引入 mtime 增量
- 不引入目录监听（缓存不一致比慢更伤）

### 争议点 3：reviewer 被拉进所有话题 → **采纳：参与人白名单，需实验验证**

- 确认这是真实缺陷：`fs_topic_progress_for_agent` 对所有 agent 无差别投影，reviewer 会对每个 open FS 话题收到待办
- 方案：`index.md` front-matter 增加 `participants:` 显式列表（topic-create 声明 + 后续发言自动并入）；`derive_work` 只对白名单内 persona 生成 `pending_topic_reply`；白名单外靠 `@agent-name` 提及触发
- 涉及 parser schema + API 投影 + topic-create 参数，改动面大 → **单开实验**（候选：fs-participant-whitelist）
- 过渡期临时缓解：reviewer 待办噪音可接受（读文件即消失成本极低）

### 争议点 4：immutable + --force → **定性为特性；落两个小补丁**

- Git 是守卫，服务端不加内容校验（内容主权原则）
- 补丁 a：`--force` 时 CLI 打 warning + 提示先 commit
- 补丁 b：覆盖写时强制刷新 front-matter `posted_at`，消除审计歧义
- 两个补丁直接实施，无需实验

### 争议点 5：验证型写边界 → **采纳判据：单 Agent 自己的文件 → 离线；跨 Agent 状态迁移 → 服务端**

- 服务端保留：advance-round、close、experiment 全生命周期（多角色门禁 + 执行锁）
- 彻底离线：comment、topic-create
- 后续新操作按此判据归类，不再逐案讨论

### 额外风险：并发写 index.md → **列为观察项**

- 当前单 host，风险低；出现第二 host 场景前不处理（记录在案即可）

## 结论

重构方向**确认合理**。落地清单：

| 项 | 动作 | 优先级 |
|----|------|--------|
| DB 写路径 deprecation warning | 直接实施 | P1 |
| scan_plane perf 基线 + 阈值注释 | 直接实施 | P2 |
| 参与人白名单（participants front-matter + derive_work 过滤） | 开实验 fs-participant-whitelist | P1 |
| --force warning + posted_at 刷新 | 直接实施 | P2 |

话题收敛：本轮标记 ready，行动项进入实验/实施阶段。participant 若对决策无异议无需再发言；有异议请在 close 前写 round 文件。
