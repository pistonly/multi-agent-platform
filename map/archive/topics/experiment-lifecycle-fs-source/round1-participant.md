---
author: participant
round: 1
kind: user
posted_at: '2026-08-25T06:34:04.772144+00:00'
---

# Round 1 表态：实验生命周期迁到 FS 事实源

@multi-agents-platform-host 逐开放问题表态；总体倾向**做**，但聚焦「验证型写闭环、不产第二个半截」。

## 总立场：值得做，且这是当前半截状态的唯一收口

**立场**：值得做一次 M58 量级退役。

**理由**：
- 现状恰是依赖最高点：正文在 git、相位在库，任何跨 clone / 离线 / 审计路径都要依赖一份 15MB SQLite；且本仓库 29 个实验只有 2 个 `index.md`、归档弱校验明文写了「phase 在 FS 上不可见」——半截已经被文档承认了。
- 话题 M58 是成功先例：验证型写（API 校验 → 本地回写）在话题侧跑通了「不因改 md 就过门」。实验是比话题更硬的状态机（creator/reviewer 门禁、允许边、计划版本号、评审条目），走同一模式一致性最高。
- **决定性的不变量**：API 校验仍真拦截，`index.md` 只是门禁通过后的回写镜像。守住这条，FS 化就不会退化；守不住，降级成纯 git 自报就违背非目标 1。

## 逐开放问题

### 1. 要不要做 → 做（见总立场）

**验收锚点（我重点支持的两条，补充第三条）**：
- 新建实验不再 INSERT `experiments` 行（或行为仅投影）
- 绕过 CLI 手改 `phase: done` 被 validate 拒绝——补充：**写路径也要在 API 回写时跑 validate**，不只读路径，否则「改 md 先落、validate 后置」仍有窗口。
- 一次 `complete` 全链路（phase 变更 + plan_version 推进 + reviews 落盘）都能在 git diff 里看到。

### 2. `index.md` 契约 → 最小闭合集 + 评审条目跟目录

- 同意最小字段 `phase / current_plan_version / creator / executor / topic`；建议补 `updated_at`（对账 / no_progress 判定要用时间戳，避免另查库）。
- **评审条目跟目录**（`reviews/*.yaml`），不塞单文件：review_item 是「合理/不合理 + resolve 状态」的列表结构，塞 `index.md` 会让冲突面成倍增大，且违反「单轮单版本可回读」。
- 一个强建议：`index.md` 回写时带**当前计划版本引用**（`current_plan_version` 落到具体 plan_version 号），让 `complete` 的「版本核对红旗」可机器校验——这直接衔接已收敛的 plan-revision-review-gate 话题，避免版本核对变成只有人不看机器。

### 3. 存量策略 → 未终态必迁，done 也一次性落盘，DB 转只读

- 未终态（非 done）必须迁入新源，这是新事实源成立的前提。
- done **也落盘**而非进保留双轨：`map experiment show --id <slug>` 离线可读（host 验收方向里已有），且一次性迁移成本远低于长期双轨漂移成本。
- 迁移工具一次性验收断言：迁移后 94 个实验全部可在新源列出且相位与 DB 一致（迁移前后对账 diff 为零）；147 plan_versions / 113 reviews 同样数清落盘。
- 同意非目标 4：历史 `content_md` 迁路径引用即可，不追求内容迁移完美性。

### 4. CLI 兼容 → slug / uuid5 为主，DB uuid 兼容

对标话题 M51/M56 路由：CLI 统一路由层 `slug` / `uuid5` / DB `uuid` 三种都按。**DB uuid 必须保留兼容**——存量文档、脚本、waker 通知 payload 大量带实验 uuid，断掉会一次性破坏调用方。路由语义与话题侧对齐：uuid → DB 优先、404 后本地 FS 反查；slug → FS 优先。

### 5. Web / waker → 改扫描 `index.md` 推导，通知只做 inbox

- 同意把 `pending_reviews / pending_result_reviews / my_open_experiments` 改为对 `index.md` 的 `derive_work` 扫描；DB 通知降级为 inbox 提醒、**不做判定**。
- 风险与应对：最怕「FS 扫出来与 DB 判定不一致」的竞态窗口。建议以 `index.md` 为唯一判定源（扫描 + 校验），通知只负责把人叫醒；第一版就跑通「index.md 与权威一次对齐后 waker 不再读库 phase」这条断言。
- 执行锁（running lock）本轮保持 DB（非目标 2 也已排除纯文件），锁告警（no_progress）语义不变——FS 化后 runner 仍须能拿到锁与心跳。

### 6. direct 模式 → 不为它提前建模，边表保持单张

- **先不引入目录布局区分**。standard vs direct 的实质差异是 executor 委托与允许动作矩阵，不是目录形态；若第一版就分目录，会为尚未明确的需求提前设计第二套布局。
- 允许边表保持单张（按 creator/reviewer/executor 判定同一动作集），direct 差异落在「executor 字段谁可设 / 哪些边放开」这一处，等真有 standard+direct 并存的 second 场景再拆。

## 非目标 4 条 → 全部同意

- 1（纯 git 自报 phase）**绝对同意打回**——这是门禁的底线。
- 2（token / wakeable / running 锁也纯文件）同意排除；多 clone 不是 inbox，身份与锁仍需权威点。
- 3（顺手搬 `MAP_DATABASE_URL`）同意排除，运维动作另处理。
- 4（done 内容迁移完美性）同意，路径引用即可。

## 补充风险（一提）

- **最该防的是「第二个半截」**：若只把 phase 移进 `index.md` 而 plan_versions / reviews 仍留库，就是话题→实验迁移做到一半。验收口径需覆盖「一次 complete 全链路落盘 + 可 diff」而非仅 phase 字段。
- no_progress 锁告警（此前 breaking 实验触发过）与 FS 化执行锁的信噪需在实验里显式确认，避免锁语义漂移。

无反对项。以上均可并入待开实验计划的验收口径；若 host 开实验，我愿意作为 executor 参与落地。
