---
title: "audit 链一致性检测 + skill 写入红线 + close 语义出口（T7）：verify-audit 检测 + skill 红线 + discussion_converged 合法出口 + server 侧 _view_as_fs_topic 注入修复"
acceptance:
  - "A1 verify-audit 检测层：新增 `map fs verify-audit` 命令，扫描 `map/topics/**/index.md` + `map/topics/**/audit.jsonl` + `map/experiments/**/index.md` + `map/experiments/**/audit.jsonl`，**不**读 round<N>-*.md 内容（内容字段非状态字段）；发现 status 已变但无 audit 凭据 → 逐条指认 + drift_id 编号 + 非 0 退出；双格式（`--format json` 默认 + `--format human`）"
  - "A2 verify-audit 检测模式 5 类（每条独立报 + drift_id 编号 D001+）：(1) status=closed 但 audit.jsonl 无 close 行（T5-B 漂移场景）；(2) phase=running/done/approved 但 audit.jsonl 无对应 transition 行；(3) round 字段与 audit.jsonl 最后 round_advanced 行不一致；(4) close_reason 字段值不在合法枚举内；(5) archive/waive 字段无对应 audit 凭据"
  - "A3 verify-audit 退出码设计：`0` = 干净 / `1` = 有漂移 / `2` = 校验过程异常（如 audit.jsonl 损坏无法解析）；跨 plane 比对（local audit + server audit 任一缺失即漂移）"
  - "A4 verify-audit 修复权限隔离：**仅报告，不自动修复数据**（防 verify-audit 自己写 audit 的递归绕过问题）；修复走 host 手动 `map topic archive --force-repair`（也走 CLI 留 audit，不绕过红线）；修复路径描述同步：T7 主线修复 = 补 server 侧 _view_as_fs_topic 注入 + A12 server 侧回归测试，verify-audit 检测对象的根因叙事为『server 侧 _view_as_fs_topic 未注入 experiments 字段导致 validate_fs_close 第 4 维门禁失效漂移』"
  - "A5 skill 红线条款（runtime 中立措辞）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许；4 个 skill 都加同一段（`.cursor/skills/map-project-collab/references/wake.md` + `topic-host/SKILL.md` + `experiment-host/SKILL.md` + `experiment-reviewer/SKILL.md`），避免某 persona 漏读；『视为事故』触发条件拓宽到『发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）』"
  - "A6 skill 红线『视为事故并停止当前话题操作』三 persona 细分：participant 停止写新发言文件改发诊断评论 / host 停止 advance-round / close / 创建实验等状态变更 / reviewer 停止评审提交；触发条件 = 发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）；不是 user-facing panic 按钮，是诊断流程（看到漂移 → 停下 → 报告 → 等 host/supervisor 决定）"
  - "A7 AGENTS.md 引用段（participant §10 补充）：更新项目根级 AGENTS.md 加引用『写操作统一走 map CLI，详见 .cursor/skills/**/SKILL.md 的红线条款』；理由：AGENTS.md 是项目根级硬性规则文档，比 skill 优先级高，未来新增 agent 可能不知道 skill 红线条款"
  - "A8 wake.md 引用 feedback memory：`.cursor/skills/map-project-collab/references/wake.md` 加引用 `feedback_fs_round_file_bypass.md`（避免 participant 重复踩坑走手写 round 文件路径）"
  - "A9 discussion_converged 合法 close 出口：扩展 close_reason 枚举（含 `experiment_ready` / `experiment_done` / `cancelled` / `discussion_converged` 四值）；close_note 三必填字段（`experiment_id` 可为 `none` / `followup_gate` 不可空 / `drift_ack` 可选）；sdk/python/map_fs/validation.py:179 第 4 维同步扩展；cli/commands/fs.py close CLI 校验链扩展"
  - "A10 边界与白名单：verify-audit 只读不改任何写路径（防递归绕过）；不改 FS plane「本地文件真相源」架构；不引入新 waker 周期（沿用 T4 30 cycles 复用）；git diff 白名单 `^cli/`、`^sdk/`、`^server/`、`^tests/`、`^.cursor/skills/`、`^AGENTS.md`；任一子模块（T7-a/T7-b/T7-c/T7-d server 侧修复）失败则实验整体返工（明确预期管理，participant §8 风险提示）；涉及 cli/ + server/ + .cursor/skills/ 改动验收通过后由监督者重启 server 与 waker 生效"
  - "A11 server 侧 _view_as_fs_topic 注入修复（v2 新增；round3-host.md 取证更正触发的工程边界补全）：修改 `server/services/fs_source_service.py:429` `_view_as_fs_topic` 函数构造 FsTopic 时，复用 `map_fs.scan_plane`（参考同文件 :565 `fs_experiments_view` 实现模式）或 server 侧等价查询注入 `topic.experiments` 字段（filter by `experiment.topic == view.slug`），使 `validate_fs_close` 调 `validate_close` 时 `topic.experiments` 非空（包含该 topic 关联实验），第 4 维门禁『非 terminal 实验禁止 close』真正生效；与 T3 (7aeabc2e) CLI 侧 `_require_local_topic`（cli/commands/fs.py:564）的 scan_plane 注入模式对齐，确保 CLI local plane + server remote plane 单一真值同源"
  - "A12 server 侧回归测试（v2 新增；round3-host.md 取证更正触发的工程边界补全）：在 `tests/test_fs_close_server.py` 新增 fixture + 2 case ——(1) `test_remote_close_nonterminal_experiment_rejected`：构造 topic T + 关联实验 phase=pending_review + 触发 `POST /api/v1/fs/topics/{T}/close` → 断言 409 Conflict + `close_reason=fsm_violation` 或同类业务错误，证明 server 侧 `validate_fs_close` 第 4 维门禁真正生效；(2) `test_remote_close_terminal_experiment_allowed`：关联实验 phase=done → 断言 200 通过。两 case 配对覆盖『非 terminal 拒绝 + terminal 放行』完整路径；本回归测试与 A11 修复配对（pre-fix 必失败 → post-fix 必成功）"
evidence_keys:
  - "实测输出:(1) `map fs verify-audit --format json` 输出 drift_id + 路径 + 字段；(2) `map fs verify-audit --format human` 输出可读表格；(3) T5-B 漂移 fixture（复制 experiment-cost-ledger/index.md）→ 检出 D001 + 退出码 1；(4) 正常 CLI 写入 fixture → 干净 + 退出码 0；(5) audit.jsonl 损坏 → 退出码 2；(6) 4 skill grep 含红线条款；(7) AGENTS.md 含引用段；(8) close_note 缺 followup_gate → CLI 拒绝；(9) close_reason=discussion_converged + close_note 三字段完整 → 放行；(10) close_reason=invalid → 后端拒绝；(11) server 侧 fixture 关联实验 phase=pending_review + POST /api/v1/fs/topics/{slug}/close → 409 + fsm_violation（pre-fix 必失败，post-fix 必成功）；(12) 关联实验 phase=done → 200 通过"
  - "grep 核证:cli/verify_audit/ 含 scanner.py + output.py；cli/commands/fs.py 含 verify-audit 子命令；cli/simple_waker.py 含 30 cycles 周期集成点；sdk/python/map_fs/validation.py 含 close_reason 枚举扩展；4 个 skill (.cursor/skills/**/SKILL.md + wake.md) 含『禁止 Edit/Write/sed/python』措辞；AGENTS.md 含『写操作统一走 map CLI』引用段；tests/test_verify_audit.py 含 10 case (a)-(j)；tests/test_close_reason_enum.py 含枚举校验 case；server/services/fs_source_service.py:429 `_view_as_fs_topic` 注入 `topic.experiments` 字段（filter by experiment.topic == view.slug）；server/services/fs_source_service.py:1068 `validate_fs_close` 调 `validate_close(_view_as_fs_topic(view))` 后 topic.experiments 非空；tests/test_fs_close_server.py 含 2 case（非 terminal 拒绝 + terminal 放行）"
  - "pytest_summary:tests/test_verify_audit.py 10 case 全过（10 条验收 case 端到端）+ tests/test_close_reason_enum.py 3 case 全过（实验就绪/完成/取消老路径仍 OK + discussion_converged 新路径三字段校验 + 非法 close_reason 拒绝）+ tests/test_fs_close_server.py 2 case 全过（server 侧非 terminal 关联实验拒绝 + terminal 放行，post-fix 路径）；全量 pytest -q 0 failed（基线 1850 passed 只增不减，本实验净增 = 10 + 3 + 2 = 15 case）"
dependencies:
  - "话题 fs-audit-integrity-and-close-exit（37aba951）Round 1+2+3 共识收口（round3-host.md 取证更正已 ready）"
  - "既有 `cli/commands/fs.py:162`『local plane 验证型写必留审计行』机制（verify-audit 检测依据）"
  - "既有 `sdk/python/map_fs/validation.py:179` validate_close 第 4 维『非 terminal 实验禁止 close』（discussion_converged 扩展点 + server 侧门禁对象）"
  - "既有 `cli/simple_waker.py` waker 周期集成点（T4 drift hotcheck 沿用 30 cycles）"
  - "既有 `.cursor/skills/map-project-collab/references/wake.md`（最小唤醒协议，participant §1.6 反馈引用 feedback memory 源）"
  - "既有 `feedback_fs_round_file_bypass.md` memory（避免 participant 重复踩坑走手写 round 文件路径）"
  - "既有 d0c9dc5f (T4) `drift_resync_failed` 日志 schema（verify-audit WARN 对齐：`{\"event\": \"audit_drift_detected\", \"ts\": \"...\", \"drift_ids\": [\"D001\", ...], \"alert\": true}`）"
  - "既有本仓 AGENTS.md『写操作统一走 map CLI』硬性规则（skill 红线 + AGENTS.md 引用段依据）"
  - "T5-A (715202a3) waker 巡检视图 + T5-B (20d52df4) per-实验成本台账 + T6 (a8b64c20) 阈值派生 已落地链路无冲突；T7 是『门禁失效漂移检测』对 T4『数据漂移检测』的对称机制"
  - "战役 T5-B 漂移事故复盘（host round3-host.md 取证更正背景）：experiment-cost-ledger 话题 close 走 remote plane（server `validate_fs_close`），关联实验 `e63ec33e` 当时 phase=pending_review（非 terminal）本应被门禁拦截，但 `_view_as_fs_topic` 未注入 `experiments` 字段导致第 4 维门禁『空 → 放行』永远命中—— 这是 server 侧 _view_as_fs_topic 注入遗漏（T3 CLI 侧已修，server 侧遗漏）"
  - "**server 侧根因文件位置（v2 新增；round3 取证触发的依赖补全）**：(a) `server/services/fs_source_service.py:1068` `validate_fs_close` 函数（门禁入口，调用 `validate_close(_view_as_fs_topic(view), ...)`，必须修复）；(b) `server/services/fs_source_service.py:429` `_view_as_fs_topic` 视图转 FsTopic 函数（必须注入 `experiments` 字段）；(c) `server/services/fs_source_service.py:565` `fs_experiments_view`（作为 server 侧等价查询的实现参考，复用 scan_plane 注入模式）；(d) `cli/commands/fs.py:564` `_require_local_topic`（T3 7aeabc2e 已修的 CLI local plane 注入点；server 侧遗漏即 round3 根因）；(e) `tests/test_fs_close_server.py`（server 侧回归测试新增 fixture 文件）"
  - "验收通过后由监督者重启 server 与 waker 生效（server 直接以 .venv run daemon；restart 即可；无 docker 镜像 build）"
---

# T7 audit 链一致性检测 + skill 写入红线 + close 语义出口 + server 侧 _view_as_fs_topic 注入修复

## 背景

T5-B 话题 `experiment-cost-ledger` close 时关联实验 `e63ec33e` 处于 phase=pending_review（非 terminal），本应被 server 侧 `validate_fs_close` 调 `validate_close` 的第 4 维门禁（`sdk/python/map_fs/validation.py:179`『非 terminal 实验禁止 close』）拦截——但实际放行。**host round3-host.md (posted 2026-08-31T04:47:06) 监督者取证更正**：

1. **无 audit.jsonl 是 remote close 的正常表现**：普查 T1-T5 全部 6 个已关闭话题，均无本地 audit.jsonl——close 走 remote plane（server `fs_validate_close`），审计在 server 侧，不落本地文件。『无 audit.jsonl』不能作为手写证据。
2. **真正的根因**：server 侧 `validate_fs_close`（`server/services/fs_source_service.py:1068`）调 `validate_close(_view_as_fs_topic(view), ...)`，但 `_view_as_fs_topic`（同文件 :429）构造 FsTopic 时**未注入 `experiments` 字段**——`topic.experiments` 恒为空列表，第 4 维门禁『空 → 放行』永远命中。T3 实验（b3e1924）只给 CLI local plane 的 `_require_local_topic`（`cli/commands/fs.py:564`）做了 scan_plane 注入，server 侧遗漏。
3. **host 行为合规**：T5-B 与 T7 的 close 均走 `map topic close` 正规 CLI，只是 server 门禁有洞放行了非 terminal 实验关联的话题。

**已定论边界**（host round1-host.md + participant round1 共识）：**不做 runtime 私有权限层**（如 claude settings.json permissions deny）—— 平台 runtime 中立（claude/cursor/人均可 host），不为单一 runtime 维护私有配置。门禁拦不住有文件写权限的 agent，防范 = **检测 + 红线 + 合法出口 + server 侧门禁修复**，不是拦截。

## 任务

T7 三件套合并为 1 个实验（避免 3 个独立实验引入额外 review/approve/start 周期；participant §8 接受度评估确认）：

### T7-a: verify-audit 检测层（I1-I3）

新增 `map fs verify-audit` 只读命令 + 扫描 map/topics/ + map/experiments/ 的 index.md + audit.jsonl 比对，发现漂移逐条指认 + drift_id 编号 + 非 0 退出；waker 周期集成（T4 30 cycles 复用）WARN 不阻断。

### T7-b: skill 红线层（I4-I6）

4 个 skill 加 runtime 中立红线条款（禁止 Edit/Write/sed/python 等）+ wake.md 引用 feedback memory + AGENTS.md 引用段（participant §10 补充建议）。

### T7-c: discussion_converged 合法出口层（I7-I9）

扩展 close_reason 枚举（含 `discussion_converged`）+ close_note 三必填字段（experiment_id / followup_gate / drift_ack）+ 后端 validate_close 第 4 维同步扩展。

### T7-d: server 侧 _view_as_fs_topic 注入修复（I10-I11，v2 新增）

根据 round3-host.md 监督者取证更正，补 server 侧 `_view_as_fs_topic` 注入 `experiments` 字段（复用 scan_plane 或 server 侧 `fs_experiments_view` 模式），并新增 server 侧回归测试覆盖『非 terminal 关联实验 → 409』完整路径。

## 实施步骤

### I1 cli/verify_audit/scanner.py（检测层核心）

- 新建 `cli/verify_audit/__init__.py` + `cli/verify_audit/scanner.py`
- 数据源：`map/topics/**/index.md` + `map/topics/**/audit.jsonl` + `map/experiments/**/index.md` + `map/experiments/**/audit.jsonl`
- **不**读 `round<N>-*.md` 内容（内容字段非状态字段）
- 不变量字段白名单：`status` / `phase` / `round` / `close_reason` / `archived` / `waive_reason`
- 检测模式 5 类（每条独立报 + drift_id 编号 D001+）：
  1. status=closed 但 audit.jsonl 无 close 行（T5-B 漂移场景）
  2. phase=running/done/approved 但 audit.jsonl 无对应 transition 行
  3. round 字段与 audit.jsonl 最后 round_advanced 行不一致
  4. close_reason 字段值不在合法枚举内
  5. archive/waive 字段无对应 audit 凭据
- 跨 plane 比对：local audit.jsonl + server audit（remote plane 端点如有）→ 任一缺失即漂移
- **不**写 audit（防递归绕过，选项 A 工程实现）

### I2 cli/verify_audit/output.py（双格式 + 退出码）

- 新建 `cli/verify_audit/output.py`
- 双格式：`--format json`（默认，便于 waker 集成 + grep/jq 消费）/ `--format human`（人类可读表格）
- 退出码设计：`0` = 干净 / `1` = 有漂移 / `2` = 校验过程异常（如 audit.jsonl 损坏无法解析）
- drift_id 编号格式：`D001, D002, ...`（每条独立编号，便于监督者按 ID 修复或豁免）
- 输出 schema（json）：
  ```json
  {
    "status": "drift_detected",
    "drift_count": 2,
    "drifts": [
      {"drift_id": "D001", "path": "map/topics/experiment-cost-ledger/", "field": "status", "current_value": "closed", "expected_audit_event": "close", "hint": "server 侧 _view_as_fs_topic 未注入 experiments 字段导致 validate_fs_close 第 4 维门禁失效"},
      ...
    ]
  }
  ```

### I3 cli/commands/fs.py + cli/simple_waker.py 集成

- 新增 `map fs verify-audit` CLI 子命令（注册到 `cli/commands/fs.py`）
- waker 周期集成：`cli/simple_waker.py` 加 30 cycles ≈ 15 分钟周期触发（沿用 T4 drift hotcheck 节奏，不引入新周期）
- WARN 日志格式与 `drift_resync_failed` 对齐：`{"event": "audit_drift_detected", "ts": "...", "drift_ids": ["D001", "D002"], "alert": true}`
- 不阻断 waker 轮询（仅 WARN）

### I4 4 skill 加红线条款（runtime 中立措辞）

- 修改 `.cursor/skills/map-project-collab/references/wake.md`：加段落
  > **红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许。
- 修改 `.cursor/skills/topic-host/SKILL.md`：加同样段落
- 修改 `.cursor/skills/experiment-host/SKILL.md`：加同样段落
- 修改 `.cursor/skills/experiment-reviewer/SKILL.md`：加同样段落
- **措辞完全一致**（统一从 `lib/red_line_clause.py` 字符串常量导入，避免 4 处副本漂移）

### I5 wake.md 引用 feedback memory（participant §1.6）

- 修改 `.cursor/skills/map-project-collab/references/wake.md`：加引用
  > **相关 feedback memory**：`feedback_fs_round_file_bypass.md`（避免 participant 重复踩坑走手写 round 文件路径）

### I6 AGENTS.md 引用段（participant §10 补充）

- 修改 `AGENTS.md`：加引用段
  > **写操作统一走 map CLI**（硬性规则）：所有 `map/**` 下文件的状态变更必须通过 `map` CLI（`map topic comment` / `map topic advance-round` / `map topic close` / `map experiment create` 等）；禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 `.cursor/skills/**/SKILL.md` 的红线条款。
- 理由：AGENTS.md 是项目根级硬性规则文档，比 skill 优先级高；未来新增 agent 可能不知道 skill 红线条款

### I7 sdk/python/map_fs/validation.py close_reason 枚举扩展

- 修改 `sdk/python/map_fs/validation.py`：第 4 维 validate_close 扩展 close_reason 合法枚举
- 新增 `discussion_converged` 枚举值（与原 `experiment_ready` / `experiment_done` / `cancelled` 并列）
- 非法值 → 拒绝（仅四值合法）

### I8 cli/commands/fs.py close_note 三必填字段校验

- 修改 `cli/commands/fs.py`：close CLI 校验链扩展
- close_reason=discussion_converged 时校验 close_note 三必填字段：
  - `experiment_id`：必填，可为 `none`（不开实验直接归档）
  - `followup_gate`：必填，描述闭环追踪（不可空字符串）
  - `drift_ack`：可选字段，仅当 close 触发前已发现 audit 链漂移时填写（审计透明，含 server 侧门禁失效场景）
- 缺字段 → 拒绝（与验收 case b/i 一致）

### I9 回归测试（client 端）+ commit + log + release

- 新建 `tests/test_verify_audit.py`：10 case (a)-(j)（与 A1-A10 验收对齐）
- 新建 `tests/test_close_reason_enum.py`：3 case（实验就绪/完成/取消老路径 + discussion_converged 新路径 + 非法拒绝）
- 全量 pytest -q 0 failed（基线 1850 passed 只增不减，本段净增 = 10 + 3 = 13 case）
- ruff check 0
- 窄 commit + log

### I10 server 侧 _view_as_fs_topic 注入（v2 新增；round3-host.md 取证更正触发的工程边界补全）

- 修改 `server/services/fs_source_service.py:429` `_view_as_fs_topic` 函数：构造 FsTopic 时，复用 `map_fs.scan_plane`（参考同文件 :565 `fs_experiments_view` 实现模式）或 server 侧等价查询，过滤 `e.topic == view.slug` 的实验列表，注入 `topic.experiments` 字段
- 与 T3 (7aeabc2e) CLI 侧 `_require_local_topic`（cli/commands/fs.py:564）的 scan_plane 注入模式对齐，确保 CLI local plane + server remote plane 单一真值同源
- 保持 FsTopic schema（`sdk/python/map_fs/parser.py:152` `experiments: list[FsExperiment] = field(default_factory=list)`）不变
- 注入失败处理：扫描失败 → 降级返回空 list（保持向后兼容 + 在 server 日志 WARN 记录原因），不阻断 close 流程（fail-safe open + WARN）
- **不**重写整个 server 侧，只在 _view_as_fs_topic 一处补 1 字段注入，最小改动原则

### I11 server 侧回归测试（v2 新增；round3-host.md 取证更正触发的工程边界补全）

- 新建 `tests/test_fs_close_server.py`，配对覆盖『非 terminal 拒绝 + terminal 放行』完整路径：
  - `test_remote_close_nonterminal_experiment_rejected`：构造 topic T + 关联实验 phase=pending_review + 通过 FastAPI TestClient 触发 `POST /api/v1/fs/topics/{T}/close` → 断言 HTTP 409 + `close_reason=fsm_violation` 或同类业务错误（验证 server 侧 `validate_fs_close` 第 4 维门禁真正生效）
  - `test_remote_close_terminal_experiment_allowed`：关联实验 phase=done → 断言 HTTP 200 + 写入成功
- fixture 模式：参考 `tests/test_close_reason_enum.py` 已有的本地 plane fixture，新增 server 端用 `sqlite:///:memory:` + minimal session 注入；或更轻量 — 直接调 `validate_fs_close(db, project, slug, agent)` 服务端函数（避免启动 FastAPI app），断言返回 ConflictError / 成功
- 本测试与 A11 修复配对：pre-fix 必失败（即使调用方期望 409，server 实际返回 200 因为 topic.experiments 为空）→ post-fix 必成功（topic.experiments 注入后第 4 维门禁命中）
- 全量 pytest -q 0 failed（基线 1850 + 13 + 2 = 1865 passed，本实验净增 15 case）

### I12 验收收口 + complete + release lock

- 全量 pytest -q 0 failed（基线 1865 passed，本实验净增 15 case）
- ruff check 0
- 窄 commit + experiment log + `experiment complete` → reviewer → done
- release lock

## 验收

A1-A12 已在 frontmatter 详列。**任一子模块（T7-a/T7-b/T7-c/T7-d）失败则实验整体返工**（participant §8 风险提示，明确预期管理）。

## 风险与边界

- 不做 runtime 私有权限层（settings.json deny 等）—— 已拍板
- verify-audit 只读不改任何写路径（防递归绕过，是选项 A 工程实现）
- 不改 FS plane「本地文件真相源」架构
- skill 红线条款措辞 runtime 中立（枚举是示例而非白名单；4 处统一从 `lib/red_line_clause.py` 字符串常量导入避免副本漂移）
- 不引入新 waker 周期（沿用 T4 drift hotcheck 30 cycles，约 15 分钟）
- 涉及 cli/ + sdk/ + server/ + .cursor/skills/ 改动，验收通过后由监督者重启 server 与 waker 生效（server 跑 .venv daemon，restart 即可；无 docker 镜像 build）
- **白名单包含 `^server/` + `^AGENTS.md`**（v2 新增；server 侧 _view_as_fs_topic 注入修复 + AGENTS.md 引用段）
- **T5-B 现状维持（v2 新增）**：T5-B 话题 `experiment-cost-ledger` 的 close 状态（关联实验 `e63ec33e` phase=pending_review 时被放行）维持现状不回头修——close 行为合规（host 走 `map topic close` 正规 CLI），仅因 round3 取证发现 server 侧 `_view_as_fs_topic` 未注入 experiments 字段导致门禁失效而放行；该状态由本实验 I10/I11 server 侧注入修复 + discussion_converged 合法出口语义覆盖后续场景；未来 server 侧修复 + I11 回归测试通过后不会再出现同类漂移

## §派生公式设计理由（plan §派生公式 显式记录）

为什么 verify-audit 选择『仅报告』而非『自动修复』：

- **递归绕过风险**：检测层修改数据会再次绕过审计链（『verify-audit 自己写 audit』递归问题）—— 选 A 的根本原因
- **修复权限归 host**：让 host 看到报告后决定 `map topic archive --force-repair` 之类的人工修复命令（也走 CLI 留 audit，避免『修复层也写 audit』绕过红线）；server 侧门禁修复同样走 plan 评审 + 实施步骤（I10/I11），不依赖 verify-audit 自动修复
- **保留数据可信度**：自动修复会让『未审计行』问题从检测层向修复层蔓延，破坏『检测 = 只读』的不变量
- **保留 host 人权**：检测层只暴露问题，决策权（含修复 vs 豁免）归 host，与『红线是 soft control + 检测是 hard control』的双层设计哲学一致

## 主题联动（防御四层 + 两个对称机制）

| 层 | 实验/话题 | 职责 |
|----|-----------|------|
| 阻止层 | T3 (7aeabc2e) | validate_close 门禁（CLI local plane 已注入；server remote plane 由 T7-d 补注入） |
| **修复层** | **T7-d（本实验 I10-I11）** | **server 侧 _view_as_fs_topic 注入 + server 侧回归测试** |
| **检测层** | **T7-a（本实验 I1-I3）** | **verify-audit 只读检测漂移 + waker 周期自检 WARN** |
| **合法路径层** | **T7-c（本实验 I7-I9）** | **discussion_converged 给『讨论收敛但未 terminal』提供合规出口** |

| 漂移类型 | 实验/话题 | 检测机制 |
|---------|-----------|----------|
| 数据漂移 | d0c9dc5f (T4) | skill 副本 vs 源（周期自检 + WARN + 自动重同步） |
| **门禁失效漂移** | **T7-d + T7-a（本实验）** | **server 侧 _view_as_fs_topic 注入 + server 侧回归测试（周期自检 + WARN + 报告不修复）** |

两个对称机制共享『周期自检 + WARN 不阻断』框架：T4 检测 skill 副本漂移，T7-a 检测 audit 链漂移（含 server 侧门禁失效导致的非手写场景），T7-d 修复 server 侧门禁注入遗漏。

## §派生实验创建门禁（participant §6 补充建议）

`--executor <agent>` 委派后，该 agent 临时获得 experiment-host 视角的实现权限（Edit/Write + 测试执行），但其 topic-participant 视角不变。

## v2 修订日志（plan review round 1 触发）

revise_plan v2 由 host 于 2026-08-31 在 reviewer 9 项不合理项（U1-U9）反馈后提交，主要变更：

- **U1 §背景** 第 1 段措辞纠正：事件定性从『host 手写绕过』改为『server 侧 _view_as_fs_topic 未注入 experiments 字段导致 validate_fs_close 第 4 维门禁失效』，引用 round3-host.md 取证更正
- **U2 §验收 A4 + A5** 叙事对齐 round3 根因：A4 修复路径描述同步补 server 侧 _view_as_fs_topic 注入 + A12 server 侧回归测试；A5『视为事故』触发条件拓宽到『发现 audit 链漂移（含 server 侧门禁失效导致的非手写场景）』
- **U3 §验收** 新增 **A11**：server 侧 _view_as_fs_topic 注入条款
- **U4 §验收** 新增 **A12**：server 侧回归测试条款
- **U5 §依赖** 新增 server 侧 5 个文件位置：(a) fs_source_service.py:1068 validate_fs_close 入口 + (b) :429 _view_as_fs_topic 注入点 + (c) :565 fs_experiments_view 实现参考 + (d) cli/commands/fs.py:564 CLI 注入点 + (e) tests/test_fs_close_server.py 新增 fixture
- **U6 §实施步骤** 新增 **I10**（server 侧修复）+ **I11**（server 侧回归测试），排在原 I9 之后、原收尾步骤前
- **U7 §风险与边界** 新增 T5-B 现状维持说明
- **U8 §主题联动** 标签修正：『权限漂移』→『门禁失效漂移』；同步新增『修复层』= T7-d，『阻止层』补 server 侧遗漏说明
- **U9 index.md description 字段**：当前 index.md 已 closed，本条作为未来 plan revise 触发 index.md reopen 时的建议条目同步修改；close_note 维持现状（仅描述实验创建状态），description 字段要求改为『server 侧 _view_as_fs_topic 未注入 experiments 字段导致 validate_fs_close 第 4 维门禁失效漂移的防范（round3 监督者取证更正）：verify-audit 只读检测 + skill 红线（runtime 中立）+ discussion_converged 合法 close 出口 + server 侧 _view_as_fs_topic 注入修复』
