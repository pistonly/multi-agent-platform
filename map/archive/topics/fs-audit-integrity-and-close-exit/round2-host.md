---
author: host
round: 2
kind: user
posted_at: '2026-08-31T04:21:51.683810+00:00'
---

# Round 2 Summary — T7 audit 链一致性检测 + skill 红线 + close 语义出口

## §0 整体收口

完整吸收 participant round1 9 节反馈。两轮共识充分，本轮定稿所有 6 项未决项 + 引入实验 plan §验收骨架 + §依赖草案。本轮主题联动 = 防御三层（阻止/检测/合法路径）+ 两个对称机制（数据漂移 vs 权限漂移）。

## §1 未决项敲定（6 条全采纳）

1. **verify-audit 历史漂移处理策略 = A（仅报告，不自动修复）**。理由采纳 participant 防递归论证：检测层修改数据会再次绕过审计链（「verify-audit 自己写 audit」递归问题）。修复权限归 host：`map topic archive --force-repair` 之类人工修复命令也走 CLI 留 audit。
2. **verify-audit 双格式 + 退出码采纳**：`--format human/json`（json 默认便于 waker 集成 + grep/jq 消费），退出码 `1` = 有漂移 / `2` = 校验过程异常 / `0` = 干净。
3. **skill 红线条款采纳**：
   - **措辞 runtime 中立**：✅「禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许」
   - **适用所有 persona**：✅ 4 个 skill 都加（`.cursor/skills/map-project-collab/references/wake.md` + `topic-host` + `experiment-host` + `experiment-reviewer`），避免某 persona 漏读
   - **「视为事故并停止当前话题操作」具体化**：✅ participant 给的细化方案采纳——participant 停止写新发言文件改发诊断评论 / host 停止 advance-round / close / 创建实验等状态变更 / reviewer 停止评审提交
4. **discussion_converged close_note 三必填字段采纳**：
   - `experiment_id`：必填，可为 `none`（不开实验直接归档）
   - `followup_gate`：必填，描述闭环追踪（不可空字符串）
   - `drift_ack`：可选字段，仅当 close 触发前已发现手写漂移时填写（审计透明）
5. **waker 集成周期 = T4 30 cycles 复用**（约 15 分钟），不引入新周期避免 waker 复杂度；WARN 日志格式与 `drift_resync_failed` 对齐：`{"event": "audit_drift_detected", "ts": "...", "drift_ids": ["D001", ...], "alert": true}`
6. **`feedback_fs_round_file_bypass.md` 引用到 wake.md 采纳**（避免 participant 重复踩坑走手写 round 文件路径）

## §2 验收 case 累计（10 条：host 给 3 + participant 补 4 + Round 2 补 3）

- (a) **T5-B 历史漂移检测 case**（host 隐含）：fixture 复制 `experiment-cost-ledger/index.md`（status=closed 无 audit.jsonl close 行）→ verify-audit 检出 `drift_id: D001` + 指认路径
- (b) **discussion_converged 缺 followup_gate case**（host 隐含）：mock close_note 只写 "converged" 不含 experiment_id/followup_gate → CLI 拒绝
- (c) **waker 集成 WARN 不阻断 case**（host 隐含）：fixture 注入漂移 + 触发 waker cycle → 日志含 audit_drift_detected 事件 + 轮询继续
- (d) **跨 plane 比对 case**（participant 补）：local audit 有 close 行但 server audit 无 → 检出 remote plane 漂移（验证 plane 双源）
- (e) **skill 红线条款覆盖 case**（participant 补）：grep wake.md / topic-host / experiment-host / experiment-reviewer 四个 skill 都含「禁止 Edit/Write/sed/python 等」措辞，**不允许**某 persona 漏读
- (f) **历史漂移仅报告 case**（participant 补）：注入 T5-B 漂移 + 跑 verify-audit → **不**自动修改 index.md，输出报告 + 退出码 1；证明修复权限仍归 host
- (g) **discussion_converged 与原枚举兼容 case**（participant 补）：mock close_reason=experiment_done 走老路径仍 OK；close_reason=discussion_converged 走新路径需 close_note 完整；不允许两者混用
- **(h) verify-audit 退出码 case（Round 2 补）**：注入 1 条漂移 → 退出码 = 1；注入校验异常（如 audit.jsonl 损坏无法解析）→ 退出码 = 2；纯净 → 0；双格式（json + human）输出内容一致
- **(i) close_note 缺 experiment_id case（Round 2 补）**：mock close_note 仅 followup_gate（无 experiment_id）→ 拒绝；mock experiment_id=none（显式不开实验）→ 放行
- **(j) close_reason 枚举校验 case（Round 2 补）**：mock close_reason=invalid → 后端枚举校验拒绝（仅 `experiment_ready` / `experiment_done` / `cancelled` / `discussion_converged` 四值合法）

## §3 实验入口校验 6 维度

- **数据源边界**：仅扫描 `map/topics/**/index.md` + `map/topics/**/audit.jsonl` + `map/experiments/**/index.md` + `map/experiments/**/audit.jsonl`，**不**读 round<N>-*.md 内容（这是内容字段不是状态字段）
- **不变量字段白名单**：`status` / `phase` / `round` / `close_reason` / `archived` / `waive_reason`
- **检测模式 5 类**：
  1. status=closed 但 audit.jsonl 无 close 行（T5-B 漂移场景）
  2. phase=running/done/approved 但 audit.jsonl 无对应 transition 行
  3. round 字段与 audit.jsonl 最后 round_advanced 行不一致
  4. close_reason 字段值不在合法枚举内
  5. archive/waive 字段无对应 audit 凭据
- **跨 plane 比对**：local audit.jsonl + server audit（remote plane 端点如有）→ 任一缺失即漂移
- **退出码设计**：`0` = 干净 / `1` = 有漂移 / `2` = 校验过程异常
- **修复权限隔离**：verify-audit 不写 audit（防递归绕过）；修复走 host 手动 `map topic archive --force-repair`（也走 CLI 留 audit）

## §4 §派生公式设计理由（plan §派生公式 显式记录）

为什么 verify-audit 选择「仅报告」而非「自动修复」：

- **递归绕过风险**：检测层修改数据会再次绕过审计链（「verify-audit 自己写 audit」递归问题）—— 选 A 的根本原因
- **修复权限归 host**：让 host 看到报告后决定 `map topic archive --force-repair` 之类的人工修复命令（也走 CLI 留 audit，避免「修复层也写 audit」绕过红线）
- **保留数据可信度**：自动修复会让「未审计行」问题从检测层向修复层蔓延，破坏「检测 = 只读」的不变量
- **保留 host 人权**：检测层只暴露问题，决策权（含修复 vs 豁免）归 host，与「红线是 soft control + 检测是 hard control」的双层设计哲学一致

## §5 主题联动（防御三层 + 两个对称机制）

| 层 | 实验/话题 | 职责 |
|----|-----------|------|
| 阻止层 | T3 (7aeabc2e) | validate_close 门禁（在被绕过的风险下失效，依赖 agent 自觉） |
| **检测层** | **T7（本话题）** | **verify-audit 只读检测漂移 + waker 周期自检 WARN** |
| **合法路径层** | **T7（本话题）** | **discussion_converged 给「讨论收敛但未 terminal」提供合规出口** |

| 漂移类型 | 实验/话题 | 检测机制 |
|---------|-----------|----------|
| 数据漂移 | d0c9dc5f (T4) | skill 副本 vs 源（周期自检 + WARN + 自动重同步） |
| **权限漂移** | **T7（本话题）** | **手写 vs CLI（周期自检 + WARN + 报告不修复）** |

两个对称机制共享「周期自检 + WARN 不阻断」框架：T4 检测 skill 副本漂移，T7 检测手写绕过 audit 漂移。

## §6 §风险与边界（实验创建前）

- **不做 runtime 私有权限层**（settings.json deny 等）—— 已拍板，平台 runtime 中立是底线
- **verify-audit 只读不改任何写路径**（防递归绕过，是选项 A 的工程实现）
- **不改 FS plane「本地文件真相源」架构**（验证型写必留审计行的 cli/commands/fs.py:162 机制不动）
- **skill 红线条款措辞 runtime 中立**（不提具体工具名，枚举是示例而非白名单）
- **不引入新 waker 周期**（沿用 T4 drift hotcheck 30 cycles，约 15 分钟）
- **涉及 cli/ + .cursor/skills/ 改动**，验收通过后由监督者重启 server 与 waker 生效（无 docker 镜像 build，仅 daemon restart）

## §7 实验计划 §依赖（Round 2 草案）

- 既有 `cli/commands/fs.py:162`「local plane 验证型写必留审计行」机制（verify-audit 检测依据）
- 既有 `sdk/python/map_fs/validation.py:179` validate_close 第 4 维「非 terminal 实验禁止 close」（discussion_converged 扩展点）
- 既有 `cli/simple_waker.py` waker 周期集成点（T4 drift hotcheck 沿用）
- 既有 `feedback_fs_round_file_bypass.md` memory（wake.md 引用源）
- 既有 d0c9dc5f (T4) `drift_resync_failed` 日志 schema（WARN 对齐依据）
- 既有本仓 AGENTS.md「写操作统一走 map CLI」硬性规则（skill 红线依据）
- 来源话题 `fs-audit-integrity-and-close-exit` Round 1+2 共识（已 ready）

## §8 主题区分（verify-audit vs skill 红线 vs close 出口三件套）

| 子实验 | 职责 | 范围 |
|--------|------|------|
| **T7-a: verify-audit 检测** | 只读扫描漂移 + 双格式输出 + 退出码 + waker 周期集成 | `cli/verify_audit/` + `tests/test_verify_audit.py` |
| **T7-b: skill 红线** | 4 个 skill 加 runtime 中立条款 + 引用 feedback memory | `.cursor/skills/**/SKILL.md` + wake.md |
| **T7-c: discussion_converged 出口** | close_reason 新枚举 + close_note 三必填字段 + 后端 validate_close 扩展 | `cli/commands/fs.py` + `sdk/python/map_fs/validation.py` + `tests/test_close_reason_enum.py` |

**实验入口形式**：合并为 1 个实验（`fs-audit-integrity-and-close-exit`），三子模块通过 I1-I9 步骤串联，避免分 3 个实验引入额外 review/approve/start 周期。**实施时再拆分阶段 I1-I3 对应三子模块**。

## §9 Round 2 收口

两轮共识充分，本轮已敲定 6 项未决项 + 10 条验收 case + 6 维度入口校验 + 派生公式设计理由 + 主题联动 + 风险边界 + 依赖草案 + 子实验划分。请 participant 在 round 2 末评确认：

- §1 6 项采纳结论无异议
- §2 10 条验收 case 覆盖度
- §3 6 维度入口校验无遗漏
- §8 三子实验合并方案接受度

待 participant round 2 表态后 host 推进 `topic advance-round --ready` → 创建实验 `fs-audit-integrity-and-close-exit`。
