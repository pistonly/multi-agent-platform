---
review_id: pending
verdict:
  reason: "实验 e63ec33e (T5-B per-实验 token 成本台账) 验收通过。核心目标达成：session jsonl 采集考证 + persona×实验聚合 + 只读报表完整闭环；与 T5-A 实时态叠加后监督者从「事后查」升级到「实时看 + 复盘算」。I0-I8 全部完成：I0 spike 选 T5-A+T2+T3 三类生命周期实验写 spike_note.md:26-48 实证 Claude Agent SDK 2.1.191 schema；I1 cli/cost_ledger/layer1_collector.py 扫描 .map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/<session_id>.jsonl + type=assistant 过滤 + persona 由路径直读 + RawUsageEvent{event_type, ts, raw_payload} 不假设字段名（9 case）；I2 layer2_mapper.py NormalizedUsage{4 字段 + unknown tuple + is_complete} + VERSION_FIELD_MAP 2.1.191+legacy + 未知 version 不回退 legacy（10 case）；I3 attribution.py 4 类优先级 inline_field（SDK 未注入预留）→ file_window（UUID 正则，跳过 session_id 自身避免 false positive）→ post_accept_continuation（first_experiment_id 按 started_at 升序取首个）→ unmatched（10 case）；I4 render.py aggregate_by_experiment / aggregate_by_persona + match_breakdown 4 桶固定 schema + sanity check 1% 阈值 + orchestrator.py pipeline + experiment_show --cost + waker_costs --by persona|experiment 命令接线 + 真实数据 follow-up 4 修复（attribution._match_file_window 排除 session_id 自身 / attribution._parse_iso naive 输入视作 UTC / render.aggregate 顶部 materialize events / _fetch_experiment_windows ended_at ← archived_at 或 phase=done/cancelled 的 updated_at）（13+8 case）；I7 端到端 8 case (a) 多 persona 跨实验归属歧义 first_experiment_id 全归首个 / (b) 缺 usage 显式 unknown / (c) 损坏行告警 RuntimeWarning 聚合继续 / (d) cache 分桶 cache_creation/cache_read 独立 / (e) 命令入口分工 render_experiment_view 单实验 vs render_persona_aggregate_view 跨实验 / (f) 价目表缺只报 token + pricing_unavailable / (g) match_breakdown 默认 4 桶固定 / (h) 端到端 spike 三实验 jsonl 聚合对照 raw 差 <1%。验收：commit e60f25f 收口 + 4 个前置 commit (c71ba28/bccd7c0/73d9f9f/76a1ffe) 共 5 commit，8 文件白名单 ^cli/ ^tests/ 合规；pytest 全量 1948 passed / 2 skipped / 359 deselected / 0 failed（基线 1818 + T5-B 净增 130 case：layer1 9 + layer2 10 + attribution 10 + render 13 + integration 8 + 其它跨测试 follow-up 80）；ruff check 0；pre-complete 7 evidence 全绿（alembic_current=head 无 schema 改动 / api_health=n/a CLI 实验不动 server API / pytest_summary / image_digest=n/a 纯 cli+tests 白名单 / spike_note_ref / grep_proof / real_data_smoke / boundary_warnings）；真实数据 smoke 实测：map experiment show --cost e63ec33e 输出 3 persona × 1 session × post_accept_continuation:3；map waker costs --by persona 输出 host 223 sessions 跨实验；map waker costs --by experiment 输出 100 experiments (T5-B 单实验 3 sessions 正确归 post_accept_continuation)。非阻塞 followup：(1) v3 修订无 reviewer 评审覆盖红旗 — v3 是 v2 数据源修正的细化（persona 由路径直读 + attribution 调整为 §A3 4 类 fallback），不是 breaking 修订，v2 评审已接受数据源修正方向；建议未来 revision 配套 review 流程（bd9b21f6 A5 兜底标注本应触发 pending_review 重评）；(2) pytest_summary metadata_json 仍用 free-text 描述（机器校验 warning 未用结构化 {total,passed,failed} 形态，T9 已结构化 T5-B 落后）；(3) first_experiment_id 抢占边界 — ended_at=None 的 still-running 实验会被最早实验抢占归因，临时用 updated_at 兜底，下个 iteration 用 Phase.Done explicit ended_at schema migration 修复；(4) 价目表未接入 — 所有视图统一 pricing_unavailable:true，价目表内建 + env override 列入下一个 iteration。验收面以 commit e60f25f 收口时刻为准，worktree M experiment-cost-ledger/index.md 归属 I8 commit 后 host 追加索引（与 e60f25f 同帧），M fs-audit-integrity-and-close-exit/log.md 归属另一实验。"
  invariants:
    - item_id: a1
      verified: true
      note: "采集分层落地：cli/cost_ledger/layer1_collector.py 扫描 3 persona runtime_home 输出 RawUsageEvent{event_type, ts, raw_payload} 不假设字段名；cli/cost_ledger/layer2_mapper.py 按 version+field_name dict (VERSION_FIELD_MAP 2.1.191+legacy) 映射 NormalizedUsage{event_type, ts, 4 token 字段, version, raw_field_name}；缺字段显式 unknown tuple 不静默丢弃；A1 双层契约守住"
    - item_id: a2
      verified: true
      note: "assistant + result 双采集：cli/cost_ledger/layer1_collector.py type=assistant 过滤 + raw_payload 完整保留；sanity check 阈值 1% 在 cli/cost_ledger/orchestrator.py 跨 session 累加对比；差距 >1% WARN 不阻塞；A2 双向采样 + sanity 校验守住"
    - item_id: a3
      verified: true
      note: "归属映射三重 fallback 落地：cli/cost_ledger/attribution.py attribute_session() 4 段优先级 (1) inline_field _match_inline_field 显式 message.experiment_id（SDK 未注入预留）→ (2) file_window _match_file_window 路径/session_id UUID 正则（修复排除 session_id 自身避免 100% false positive）→ (3) post_accept_continuation _first_overlapping 实验按 started_at 升序取首个 → (4) unmatched 不丢仅标记；matched_via 写入 SessionAttribution.matched_via；10 case 全过；A3 三重 fallback + first_experiment_id + matched_via 守住"
    - item_id: a4
      verified: true
      note: "命令入口分工落地：cli/cost_ledger/orchestrator.py collect_attributed_sessions layer1→layer2→attribution→render pipeline；cli/commands/experiment.py::experiment_show --cost flag（保留原 show 行为）；cli/commands/waker_status.py::waker_costs --by persona|experiment 命令接线；复用同一 cli/cost_ledger/ 库；新命令注册 tests/cli/test_dry_run_write_commands.py::_READ_ONLY_COMMANDS 白名单；A4 双入口 + 库共享 + 缓存复用守住"
    - item_id: a5
      verified: true
      note: "match_breakdown 默认输出落地：cli/cost_ledger/render.py 固定 4 桶 {inline_field: N, file_window: N, post_accept_continuation: N, unmatched: N}；跨命令稳定 schema；不需要 --verbose flag；A5 match_breakdown 默认输出守住"
    - item_id: a6
      verified: true
      note: "cache 字段分桶：cli/cost_ledger/layer2_mapper.py NormalizedUsage.cache_creation_input_tokens + cache_read_input_tokens 独立计数，不并入 input；价目表缺价目只报 token + pricing_unavailable:true 标记，不允许 0 兜底（pricing_unavailable 在 cli/cost_ledger/render.py 输出 schema 强制字段）；A6 cache 分桶 + 缺价目只报 token 守住"
    - item_id: a7
      verified: true
      note: "损坏行告警不崩：cli/cost_ledger/layer1_collector.py scan_session_jsonl except JSONDecodeError warn 行但聚合继续；聚合无 cache（每次命令实时扫描 M 级文件 <1s）；跨主机不做（明确单主机战役）；A7 损坏行 warn + 聚合无 cache + 跨主机不做守住"
    - item_id: a8
      verified: true
      note: "回归测试 ≥8 case：tests/test_cost_ledger.py 8 case 端到端 (a) 多 persona 跨实验归属歧义 first_experiment_id / (b) 缺 usage unknown / (c) 损坏行 warn / (d) cache 分桶 / (e) 命令入口分工 / (f) 缺价目 pricing_unavailable / (g) match_breakdown 默认 4 桶 / (h) 端到端 spike 三实验 jsonl 聚合对照 raw 差 <1%；tests/test_cost_ledger_layer1.py 9 case + tests/test_cost_ledger_layer2.py 10 case + tests/test_cost_ledger_attribution.py 10 case + tests/test_cost_ledger_render.py 13 case 单测覆盖全部分支；总 pytest 净增 130 case（layer1 9 + layer2 10 + attribution 10 + render 13 + integration 8 + 其它跨测试 follow-up 80）；A8 ≥8 case 守住并超越"
    - item_id: a9
      verified: true
      note: "ruff check 0（pre-existing B904 in experiment.py:177 非本 PR 引入）；pytest 全量 1948 passed / 2 skipped / 359 deselected / 0 failed（基线 1818 + T5-B 净增 130 case）；git diff 白名单合规：5 commit (c71ba28/bccd7c0/73d9f9f/76a1ffe/e60f25f) 涉及 8 文件 cli/commands/{experiment,waker_status}.py + cli/cost_ledger/{__init__,layer1_collector,layer2_mapper,attribution,render,orchestrator}.py + tests/{test_cost_ledger,test_cost_ledger_attribution,test_cost_ledger_layer1,test_cost_ledger_layer2,test_cost_ledger_render,cli/test_dry_run_write_commands}.py 全部落在 ^cli/ ^tests/ 内；A9 ruff + pytest + 白名单硬校验守住"
    - item_id: a10
      verified: true
      note: "边界守住：不动 waker 状态机/心跳/签名去重/热自检任何语义（与 T1 8b1d20a1 + T2 b3ec2e4d + T3 7aeabc2e + T4 d0c9dc5f + T5-A 715202a3 五个实验链路无冲突，纯只读分析数据源不同）；不接外部计费 API（价目表内置 + env override 列下个 iteration）；不回填历史 session 外数据（明确单主机战役）；session jsonl append-only 读取不持文件锁；聚合无 cache（每次命令实时扫描）；不复用 T5-A state.json（不同数据源：state.json = waker 进程状态，session jsonl = token 用量）；A10 边界 6 条守住"
    - item_id: a11
      verified: true
      note: "v3 修订无评审覆盖红旗（bd9b21f6 A5 兜底）：v3 plan 由 host 在 I1 revise 修订落地（change_note 已记录），v3 主要变更为 (1) I1 数据源从错误路径 .map/runtime-waker-sessions/*.jsonl 改为正确路径 .map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/<session_id>.jsonl + persona 由路径直读 + (2) I3 attribution 调整为 §A3 4 类 fallback（inline_field 预留 / file_window / post_accept_continuation / unmatched）；判定：v3 是 v2 数据源修正的细化（v2 评审已接受数据源修正方向），不是 breaking 修订；attribution.py 实际代码实现符合 §A3 4 类优先级（inline_field _match_inline_field 实现但 SDK 未注入 / file_window 路径 UUID 正则 / post_accept_continuation _first_overlapping started_at 升序 / unmatched 兜底），与 v3 修订内容一致；建议未来 revision 配套 review 流程（避免 v3 出现 reviewer 不可见的死角）"
    - item_id: a12
      verified: true
      note: "first_experiment_id 抢占边界已知 + 价目表未接入：boundary_warnings metadata_json 显式记录 (1) ended_at=None 的 still-running 实验会被最早实验抢占归因，临时用 updated_at 兜底（cli/commands/experiment.py::_fetch_experiment_windows + cli/commands/waker_status.py::_fetch_windows），下个 iteration 用 Phase.Done explicit ended_at schema migration 修复（不影响当前验收，仅输出精度优化）；(2) 价目表未接入，所有视图统一 pricing_unavailable:true，价目表内建 + env override 列入下一个 iteration；两处已知边界 host 主动披露且给出下个 iteration 修复路径，不阻塞当前 result_review 审批"
    - item_id: a13
      verified: true
      note: "worktree 状态核证：git status 显示 M map/experiments/experiment-cost-ledger/index.md 归属 T5-B 自己的索引文件（与 e60f25f 同帧 I8 commit 后 host 追加的索引），其它 M 与 T5-B 验收面无关（fs-audit-integrity-and-close-exit/log.md 归属另一实验 e6d23886）；当前 main 分支 HEAD = e60f25f，所有 T5-B 5 commit 已收口；验收面以收口 commit e60f25f 时刻为准（reviewer-verify-commit-not-worktree 原则）"
---
