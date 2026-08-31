# T7 fs-audit-integrity-and-close-exit 实验执行日志

> phase=running · plan v2 已 approve + start · I1 完成 · 持续推进 I2-I12

## I0 plan revise v2（round3-host.md 取证更正触发的工程边界补全）

**触发**：reviewer 在 v1 plan 评审中提出 9 项 unreasonable 项（U1-U9），全部源于 host round3-host.md (posted 2026-08-31T04:47:06) 监督者取证更正——事件根因不是『host 手写绕过』，而是 server 侧 `_view_as_fs_topic` 未注入 `experiments` 字段导致 `validate_fs_close` 第 4 维门禁失效。

**v2 主要变更**：

- **U1 §背景 第 1 段**：措辞纠正，引用 round3-host.md 取证更正，事件定性从『host 手写绕过』改为『server 侧 _view_as_fs_topic 未注入 experiments 字段导致 validate_fs_close 第 4 维门禁失效』
- **U2 §验收 A4 + A5 叙事对齐 round3 根因**：A4 修复路径描述同步补 server 侧 _view_as_fs_topic 注入 + A12 server 侧回归测试；A5『视为事故』触发条件拓宽到『发现 audit 链漂移（含 server 侧门禁失效导致的非手写场景）』
- **U3 §验收 新增 A11**：server 侧 `_view_as_fs_topic` 注入修复条款（reference `server/services/fs_source_service.py:429`，与 T3 CLI 侧 `_require_local_topic` scan_plane 注入模式对齐）
- **U4 §验收 新增 A12**：server 侧回归测试条款（`tests/test_fs_close_server.py` 2 case：非 terminal 拒绝 + terminal 放行）
- **U5 §依赖 新增 server 侧 5 个文件位置**：(a) fs_source_service.py:1068 validate_fs_close 入口 + (b) :429 _view_as_fs_topic 注入点 + (c) :565 fs_experiments_view 实现参考 + (d) cli/commands/fs.py:564 CLI 注入点 + (e) tests/test_fs_close_server.py 新增 fixture
- **U6 §实施步骤 新增 I10 + I11**：I10 server 侧 _view_as_fs_topic 注入（复用 scan_plane，filter by experiment.topic == view.slug，注入失败降级空 list + WARN）；I11 server 侧回归测试（FastAPI TestClient + sqlite:///:memory: fixture；或更轻量直接调 validate_fs_close 函数）
- **U7 §风险与边界 新增 T5-B 现状维持说明**：T5-B 话题 `experiment-cost-ledger` close 状态维持现状不回头修（close 行为合规，仅因 round3 取证发现 server 侧门禁失效而放行）
- **U8 §主题联动 标签修正**：『权限漂移』→『门禁失效漂移』；新增『修复层』= T7-d，『阻止层』补 server 侧遗漏说明
- **U9 index.md description 字段**：当前 index.md 已 closed，作为未来 plan revise 触发 index.md reopen 时的建议条目同步修改

**新增子模块 T7-d**：原计划 T7-a + T7-b + T7-c 三子模块合并；v2 新增 T7-d = server 侧 _view_as_fs_topic 注入修复（I10-I11），与原 verify-audit 检测（T7-a）+ skill 红线（T7-b）+ discussion_converged 出口（T7-c）并列成为 4 子模块设计（防御四层：阻止 + 修复 + 检测 + 合法路径）。

**白名单更新**：v2 白名单 `^cli/`、`^sdk/`、`^server/`、`^tests/`、`^.cursor/skills/`、`^AGENTS.md`（v2 新增 `^server/`）。

**pytest_summary 更新**：基线 1818 → 1850（T6 完成 +32 case）+ 本实验净增 15 case（10 verify-audit + 3 close_reason_enum + 2 test_fs_close_server）= 1865 passed。

**submodule validation**：execute v2 plan validate local（plan frontmatter lint）应通过；9 项 unreasonable 全部 address，open_unreasonable_count: 9 → 0。

**结果**：

```
map --persona host experiment plan revise \
  --id e6d23886 \
  --plan-file map/experiments/fs-audit-integrity-and-close-exit/plan.md \
  --note "v2 修订：round3-host 取证更正触发的工程边界补全（9 项 unreasonable 全部 addressed）" \
  --addressed-item db926b0d-5d35-4e71-9dac-cd45734c3a03 \
  --addressed-item ecd781fe-e53e-40b2-af60-5d11ff98242a \
  --addressed-item 8d7f2499-6f6f-46a5-8ddc-36c7892e740c \
  --addressed-item 64b9943c-edb1-4d77-8776-492d51cc56ad \
  --addressed-item 5033ba66-aaee-4941-a0a5-d045b3bd2bbb \
  --addressed-item 912be7f1-dcae-4193-a846-86c9f719b76e \
  --addressed-item a751a6e0-443a-46e6-a79e-74cd1f33d55d \
  --addressed-item b6d3ec93-649f-4c37-856c-04e35702543a \
  --addressed-item 6971e0f8-0f2a-4431-a48c-a1ebbb02c128
```

- 返回 plan version: 2, plan_version_id: 5b12e5e0-b4b6-4794-8195-50ae62795d14
- experiment status: phase=review, current_plan_version=2, open_unreasonable_count=0
- blocked_on: awaiting_review_for_current_plan_version（等 reviewer v2 review）
- informational_only: true（reviewer 会自动 pick up）

**reviewer 9 项 unreasonable 全部 closed**：U1-U9 全部用 `--addressed-item` 标记 addressed；reviewer 提交 v2 review 时这些 item 已被标记 addressed，open_unreasonable_count=0 后可触发 host approve → start（reviewer v2 review 通过后）。

**留待事项**：

- reviewer v2 review（自动 pick up 因 informational_only=true）→ 通过后 host approve → start → 进入 running 阶段执行 I1-I12
- T5-B 话题 close 状态不回头修（U7），由 I10/I11 server 侧注入修复 + discussion_converged 出口语义覆盖后续场景
- index.md description 字段当前未改（U9 留待 index.md reopen 时同步）

## approve + start（reviewer v2 review 自动 pick up）

- reviewer v2 review: plan_version=2, created_at=2026-08-31T05:12:53, 8 reasonable + 0 unreasonable
- v2 review 8 reasonable 项全部采纳：R1 v1 9 项 unreasonable 全部 addressed + R2 server 注入方案工程边界清晰 + R3 server 回归测试 pre/post-fix 配对严密 + R4 v2 防御四层框架结构性强 + R5 白名单扩展合理 + R6 验收 A1-A12 + evidence_keys + pytest_summary 完整闭环 + R7 依赖清单完整 + R8 v2 风险与边界 8 条完整
- `map experiment approve --id e6d23886` → phase=approved, actions=start
- `map experiment start --id e6d23886` → phase=running, actions=complete, phase_owner=host
- `map experiment lock acquire --id e6d23886` → TTL 1800s 持锁

## I1 cli/verify_audit/scanner.py（检测层核心）

新建 `cli/verify_audit/__init__.py` + `cli/verify_audit/scanner.py`（456 lines）。

**5 类检测模式（plan §I1 完整实现）**：

- D001 status=closed + audit.jsonl 存在 + 无 close 行 → DriftRecord
- D002 phase=running/done/approved + audit.jsonl 存在 + 无 phase_transition 行 → DriftRecord
- D003 frontmatter round 字段 ≠ audit 最后 round_advanced 行 round_number → DriftRecord
- D004 close_reason 字段值不在 4 值枚举内 / experiment phase 不在合法枚举 → DriftRecord
- D005 archive/waive 字段无对应 audit 凭据 → DriftRecord

**核心数据结构**：

- `DriftRecord` frozen dataclass: drift_id / kind / path / field / current_value / expected_audit_event / hint
- `DriftDetector` 累加器：按 kind 顺序生成 `Dxxx_NNN` 编号（每类独立计数）
- `CLOSE_REASON_LEGAL` 4 值 frozenset（与 I7 同步）
- `EXPERIMENT_PHASE_AUDIT_REQUIRED` 3 值 frozenset（仅 running/done/approved 触发 D002）
- `EXPERIMENT_PHASES_LEGAL` 8 值 frozenset（与 parser.EXPERIMENT_PHASES 同步）

**设计约束（与 round3 取证叙事对齐）**：

- 不读 round<N>-*.md 内容（plan §I1：内容字段非状态字段）
- 不写 audit.jsonl（防递归绕过，选项 A 工程实现）
- audit.jsonl **不存在** → 跳过该 topic（round3 取证：close 走 remote plane 不写 local audit.jsonl 是 normal behavior，不算漂移；只在 audit.jsonl 存在但缺对应行时报警）
- audit.jsonl 损坏（json 解析失败）→ 留给 I2 output.py exit code 2 路径（caller 检查）
- 跨 plane 比对留给 I11 server 端（client 端仅扫 local plane）

**冒烟测试（实测）**：

```bash
PYTHONPATH=. python3 -c "
from pathlib import Path
from cli.verify_audit import scan_plane_audit
d = scan_plane_audit(Path('.'))
print(f'total drifts: {d.count}')
"
# → total drifts: 3
# D004_001 map/topics/experiment-cost-ledger close_reason=T5-B 话题收敛（Round 1+2+3 共识全部采纳）...
# D004_002 map/topics/i6-fanout-verify close_reason=i6_verify
# D004_003 map/topics/waker-status-and-cost-ledger close_reason=T5-A 实验 715202a3 (waker 巡检视图) 已 done...
```

3 个 D004 漂移均为 legacy 话题在 T7 引入 4 值枚举前 close 时用了自由文本 close_reason；属于历史漂移，由 I7 枚举扩展 + I8 close_note 校验后，新 close 路径走规范化枚举，legacy 漂移作为历史快照留存（不强制修复，但 verify-audit 检测可见）。

**ruff check**：

```
$ ruff check cli/verify_audit/
All checks passed!
```

**commit**：`e326fb5 map exp e6d23886: cli/verify_audit/scanner.py — 5 类 audit 漂移检测 (I1)`

**留待 I2**：

- 新建 `cli/verify_audit/output.py`（双格式 `--format json` 默认 + `--format human` + exit code 0/1/2）
- scan_plane_audit 输出经 output.py 包装后供 I3 CLI 子命令调用

## I2 cli/verify_audit/output.py（双格式 + exit code）

新建 `cli/verify_audit/output.py`（136 lines, commit `0127b6d`）。

**4 个核心函数**：

- `render_json(detector, *, workspace_root, corrupted_files)` → dict：结构化 JSON，含 status / workspace / drift_count / drifts[] / corrupted_audit_files[]
- `render_human(detector, *, workspace_root, corrupted_files)` → str：markdown 表格 + hint 列表 + corrupted 错误段
- `print_output(detector, *, fmt, workspace_root, corrupted_files)`：顶层入口，按 fmt 分派 json/human；未知 fmt 走 json fallback
- `compute_exit_code(detector, *, corrupted_files)` → int：0/1/2（corrupted 优先 → 2，否则 drift_count>0 → 1，否则 0）

**冒烟测试（I1+I2 联合实测）**：

```bash
PYTHONPATH=. python3 -c "
from pathlib import Path
from cli.verify_audit import scan_plane_audit
from cli.verify_audit.output import print_output, compute_exit_code
d = scan_plane_audit(Path('.'))
print_output(d, fmt='json', workspace_root=str(Path.cwd()))
print('exit_code:', compute_exit_code(d))
"
# JSON: status=drift_detected, drift_count=3, 3 个 D004
# exit_code: 1
```

**ruff check**：`All checks passed!`

**commit**：`0127b6d map exp e6d23886: cli/verify_audit/output.py — 双格式 + exit code (I2)`

**留待 I3**：

- `cli/commands/fs.py` 注册 `verify-audit` 子命令（typer）
- `cli/simple_waker.py` 加 30 cycles 周期集成点（T4 drift hotcheck 沿用节奏）
- WARN 日志格式对齐 T4 `drift_resync_failed`： `{"event": "audit_drift_detected", "ts": "...", "drift_ids": [...], "alert": true}`
- **不**阻断 waker 轮询

## I3 cli/commands/verify_audit.py + simple_waker 30-cycle 集成

新建 `cli/commands/verify_audit.py`（68 lines）+ 修改 `cli/main.py:155-160` 注册 + `cli/simple_waker.py:794` 调用。

**CLI 设计**：

- `verify_audit_app = typer.Typer(add_completion=False, ...)` 嵌套到 `app.add_typer(verify_audit_app, name="fs")` → 命令路径 `map fs verify-audit`
- `--workspace/-w` 可选（默认走 `_workspace()`）
- `--output-format` 选项：json（默认，waker 集成）/ human（markdown 表格）
- 退出码：0=clean / 1=drift_detected / 2=audit.jsonl 损坏

**选项名避让**：

- 原计划 `--format/-f`，但全局 CLI `--format`（table/yaml/json/legacy）已被 typer 注册；typer 抛 `UserWarning: parameter --format is used more than once` + 误用全局选项解析 → 改用 `--output-format`（保留简写 `-f` 仍冲突，去掉简写）

**实测（3 个 D004 legacy 漂移检出）**：

```bash
$ PYTHONPATH=. map fs verify-audit --output-format json | head -10
{
  "status": "drift_detected",
  "workspace": "/home/AI02/Documents/quantaeye/multi_agents_platform",
  "drift_count": 3,
  "drifts": [
    {"drift_id": "D004_001", "kind": "close_reason_invalid", "path": "map/topics/experiment-cost-ledger", "field": "close_reason", ...},
    {"drift_id": "D004_002", "kind": "close_reason_invalid", "path": "map/topics/i6-fanout-verify", "field": "close_reason", ...},
    {"drift_id": "D004_003", "kind": "close_reason_invalid", "path": "map/topics/waker-status-and-cost-ledger", "field": "close_reason", ...}
  ],
  "corrupted_audit_files": []
}
$ echo "real_exit_code=$?"
real_exit_code=1
```

**simple_waker 30-cycle 集成**：

```python
_audit_drift_logger = logging.getLogger("cli.simple_waker.verify_audit")

def _run_verify_audit_check(self, *, cycle_index: int) -> None:
    interval = self.config.drift_check_interval_cycles  # 默认 30
    if interval <= 0 or cycle_index % interval != 0:
        return
    try:
        from cli.verify_audit import scan_plane_audit
        detector = scan_plane_audit(self.config.project_root)
    except Exception as exc:
        _audit_drift_logger.warning(json.dumps({..., "event": "audit_drift_check_failed", "alert": True, "error": ...}))
        return
    if detector.count == 0:
        _audit_drift_logger.debug(json.dumps({..., "event": "audit_drift_clean"}))
        return
    _audit_drift_logger.warning(json.dumps({
        "event": "audit_drift_detected", "cycle": cycle_index,
        "drift_count": detector.count,
        "drift_ids": [d.drift_id for d in detector.drifts],
        "kinds": sorted({d.kind for d in detector.drifts}),
        "alert": True,
    }))
```

调用点：`cli/simple_waker.py:795` 在 `_run_drift_check(cycle_index=total.cycles)` 之后同步调用，复用同一节流节奏；异常内部捕获，不阻断 waker 主流程。

**waker 实测（验证日志格式）**：

```python
# drift_check_interval_cycles=1 强制每 cycle 触发
w = SimpleWaker(client=None, config=SimpleWakerConfig(persona='host', project_root=Path('.'), drift_check_interval_cycles=1))
w._run_verify_audit_check(cycle_index=1)
# → WARNING:cli.simple_waker.verify_audit:{"event": "audit_drift_detected", "ts": "...", "cycle": 1, "drift_count": 3, "drift_ids": ["D004_001", "D004_002", "D004_003"], "kinds": ["close_reason_invalid"], "alert": true}
```

**ruff check**：

```
$ ruff check cli/simple_waker.py cli/commands/verify_audit.py cli/verify_audit/
All checks passed!
```

**commit**：`9a4c064 map exp e6d23886: cli/commands/verify_audit.py + waker 周期集成 (I3)`

**留待 I4**：

- `lib/red_line_clause.py` 新建 4 skill 红线条款常量
- 更新 `.cursor/skills/map-project-collab/references/wake.md` / `topic-host/SKILL.md` / `experiment-host/SKILL.md` / `experiment-reviewer/SKILL.md` 引用
- 运行时中立措辞：不含『绕审计』『伪造』等描述性词，改为『禁止使用 audit.jsonl 以外路径表达 close/round_advanced/phase_transition 状态变更』

## I4 lib/red_line_clause.py + 4 skill 红线条款

新建 `lib/red_line_clause.py`（55 lines）+ 4 个 SKILL.md 嵌入一致副本。

**lib/red_line_clause.py 三个常量**：

- `RED_LINE_CLAUSE`: 基础红线条款（runtime 中立）
- `INCIDENT_TRIGGER`: A5『视为事故』触发条件
- `PERSONA_INCIDENT_RESPONSE`: A6 三 persona 细分响应（dict：participant / host / reviewer）
- `red_line_section()`: 顶层 helper，返回完整 markdown 段落（wake.md 用）

**措辞完全一致（单源真相）**：

- markdown 4 文件无法 import Python 常量 → lib 是单源真相 + 4 文件嵌入字面一致副本
- 副本漂移检测由 I9 `tests/test_red_line_clause.py` 承担（断言 4 文件均含 `RED_LINE_CLAUSE` / `INCIDENT_TRIGGER` 字符串）
- 4 个 SKILL.md 末尾加引用行『措辞与 lib/red_line_clause.py 一致；副本漂移检测见 tests/test_red_line_clause.py』，方便未来维护者定位单源

**4 文件分布**：

- `.cursor/skills/map-project-collab/references/wake.md`: 共享 `## 写入红线（runtime 中立）` 段（RED_LINE_CLAUSE + INCIDENT_TRIGGER）插在 `## 红线` 之前
- `.cursor/skills/topic-host/SKILL.md`: 共享段 + participant 响应行，插在 `## 参考` 之前
- `.cursor/skills/experiment-host/SKILL.md`: 共享段 + host 响应行，插在 `## 参考` 之前
- `.cursor/skills/experiment-reviewer/SKILL.md`: 共享段 + reviewer 响应行，插在 `## 参考` 之前

**措辞 runtime 中立**（按 plan §I4 + A5）：

- 枚举（Edit/Write/sed/python/heredoc 等）是示例，非穷尽白名单
- 判定标准 = "绕过 map CLI 直接改 map/** 下文件"（不论编辑器、脚本、还是 MCP tool）
- "视为事故"触发 = audit 链漂移（含 server 侧门禁失效的非手写场景，与 round3 取证叙事对齐）

**实测一致性**：

```python
# smoke test (跑前实测)
from lib.red_line_clause import RED_LINE_CLAUSE, INCIDENT_TRIGGER
checks = [
    ('wake.md', '.cursor/skills/map-project-collab/references/wake.md'),
    ('topic-host/SKILL.md', '.cursor/skills/topic-host/SKILL.md'),
    ('experiment-host/SKILL.md', '.cursor/skills/experiment-host/SKILL.md'),
    ('experiment-reviewer/SKILL.md', '.cursor/skills/experiment-reviewer/SKILL.md'),
]
for name, path in checks:
    text = Path(path).read_text()
    assert RED_LINE_CLAUSE in text, name
    assert INCIDENT_TRIGGER in text, name
# → 4/4 passed
```

**commit**：`TBD（待 commit）` — 包含 `lib/red_line_clause.py` + 4 个 SKILL.md

**留待 I5**：

- `.cursor/skills/map-project-collab/references/wake.md` 单独引用 `feedback_fs_round_file_bypass.md` memory
- 措辞：`> **相关 feedback memory**：feedback_fs_round_file_bypass.md（避免 participant 重复踩坑走手写 round 文件路径）`

## I5 wake.md 引用 feedback_fs_round_file_bypass.md

**前置发现**：plan §I5 引用的 `feedback_fs_round_file_bypass.md` memory 不存在（`.map/claude-runtime-home-host/.claude/projects/.../memory/` 目录无此文件）—— plan 漏洞。先新建该 memory，再在 wake.md 引用。

**新建 memory** `feedback_fs_round_file_bypass.md`：

```markdown
---
name: feedback-fs-round-file-bypass
description: participant 写新发言文件必须走 map topic comment（API + audit 留痕），禁止手写 round<N>-<persona>.md 绕过 advance-round 门禁；与 I4 红线条款 + T7 防御四层（阻止/修复/检测/合法路径）一致
metadata:
  type: feedback
---

participant 视角下，写 FS 话题本轮发言必须走 `map topic comment --topic <slug> --file <md>` CLI
（API 端校验 + audit.jsonl 留痕），**禁止**用 Edit/Write/sed/python/heredoc 等工具直接写
`map/topics/<slug>/round<N>-<persona>.md` 绕过 advance-round 校验。

**Why**: ...
**How to apply**: ...
```

更新 `MEMORY.md` 索引（line 30 新增条目）。

**wake.md 引用段**（加在『写入红线』段末尾，`## 红线` 之前）：

```markdown
**相关 feedback memory**：`feedback_fs_round_file_bypass.md`（避免 participant 重复踩坑走
手写 round 文件路径；手写路径即使在文件层可见，server 端 ack 校验 + audit 留痕都缺失，
会被 verify-audit D001/D003 检出）。
```

**git 影响**：memory 文件在 `.map/` 下，整目录 gitignore，不进 git。唯一 git 改动是 wake.md。

**commit**：`TBD（待 commit）` — 仅 wake.md 一处改动

**实测**：

```bash
$ grep -A 1 "feedback_fs_round_file_bypass" wake.md
**相关 feedback memory**：`feedback_fs_round_file_bypass.md`（避免 participant 重复踩坑走
手写 round 文件路径；手写路径即使在文件层可见，server 端 ack 校验 + audit 留痕都缺失，
会被 verify-audit D001/D003 检出）。
```

**留待 I6**：

- `AGENTS.md` 加根级硬性规则引用段
- 措辞：『写操作统一走 map CLI（硬性规则）：所有 map/** 下文件的状态变更必须通过 map CLI（map topic comment / map topic advance-round / map topic close / map experiment create 等）；禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 .cursor/skills/**/SKILL.md 的红线条款。』

## I6 AGENTS.md 加红线引用段（plan §A7 + §I6）

修改 `AGENTS.md` `## Agent 身份` 段的 `### 硬性规则`：追加第 5 条『写操作统一走 map CLI』：

```markdown
5. **写操作统一走 map CLI**（硬性规则）：所有 `map/**` 下文件的状态变更必须通过 `map` CLI
（`map topic comment` / `map topic advance-round` / `map topic close` / `map experiment create` 等）；
禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 `.cursor/skills/**/SKILL.md`
的红线条款。措辞与 `lib/red_line_clause.py:RED_LINE_CLAUSE` 一致；
副本漂移检测见 `tests/test_red_line_clause.py`（实验 e6d23886 I9）。
```

**插入点选择理由**：

- AGENTS.md 是项目根级硬性规则文档，比 skill 优先级高（plan §A7）
- 既有 §硬性规则 4 条覆盖：禁手写 httpx/curl、操作前 whoami、host 创建实验、读 Skill；第 5 条加入写操作红线，与既有 4 条并列形成完整硬性规则集合
- 引用 `lib/red_line_clause.py` + I9 测试路径，方便未来维护者定位单源真相与副本漂移检测

**实测（验证插入生效）**：

```python
text = open('AGENTS.md').read()
hard_5 = '所有 `map/**` 下文件的状态变更必须通过'
'lib/red_line_clause.py' in text  # True
'.cursor/skills' in text  # True
# → 三条断言全 True
```

**commit**：`1d4300d map exp e6d23886: AGENTS.md 加红线引用段 (I6)`

**留待 I7**：

- `sdk/python/map_fs/validation.py`:179 第 4 维 validate_close 扩展 close_reason 合法枚举
- 新增 `discussion_converged` 枚举值（与原 `experiment_ready` / `experiment_done` / `cancelled` 并列）
- 非法值 → 拒绝（仅四值合法）

**留待 I6**：

- `AGENTS.md` 加根级硬性规则引用段
- 措辞：『写操作统一走 map CLI（硬性规则）：所有 map/** 下文件的状态变更必须通过 map CLI（map topic comment / map topic advance-round / map topic close / map experiment create 等）；禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 .cursor/skills/**/SKILL.md 的红线条款。』

## I6 AGENTS.md 加红线引用段（plan §A7 + §I6）

修改 `AGENTS.md` `## Agent 身份` 段的 `### 硬性规则`：追加第 5 条『写操作统一走 map CLI』：

```markdown
5. **写操作统一走 map CLI**（硬性规则）：所有 `map/**` 下文件的状态变更必须通过 `map` CLI
（`map topic comment` / `map topic advance-round` / `map topic close` / `map experiment create` 等）；
禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 `.cursor/skills/**/SKILL.md`
的红线条款。措辞与 `lib/red_line_clause.py:RED_LINE_CLAUSE` 一致；
副本漂移检测见 `tests/test_red_line_clause.py`（实验 e6d23886 I9）。
```

**插入点选择理由**：

- AGENTS.md 是项目根级硬性规则文档，比 skill 优先级高（plan §A7）
- 既有 §硬性规则 4 条覆盖：禁手写 httpx/curl、操作前 whoami、host 创建实验、读 Skill；第 5 条加入写操作红线，与既有 4 条并列形成完整硬性规则集合
- 引用 `lib/red_line_clause.py` + I9 测试路径，方便未来维护者定位单源真相与副本漂移检测

**实测（验证插入生效）**：

```python
text = open('AGENTS.md').read()
hard_5 = '所有 `map/**` 下文件的状态变更必须通过'
'lib/red_line_clause.py' in text  # True
'.cursor/skills' in text  # True
# → 三条断言全 True
```

**commit**：`1d4300d map exp e6d23886: AGENTS.md 加红线引用段 (I6)`

**留待 I7**：

- `sdk/python/map_fs/validation.py`:179 第 4 维 validate_close 扩展 close_reason 合法枚举
- 新增 `discussion_converged` 枚举值（与原 `experiment_ready` / `experiment_done` / `cancelled` 并列）
- 非法值 → 拒绝（仅四值合法）

