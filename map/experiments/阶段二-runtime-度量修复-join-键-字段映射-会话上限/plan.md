---
title: "阶段二：runtime 度量修复（join 键 + 字段映射 + 会话上限）"
acceptance:
  - "A1 字段映射现代化：`cli/cost_ledger/layer2_mapper.py` 的 `_resolve_field_map` 改为「现代 schema 默认表 + legacy 例外表 + 字段名探测」：未知版本（如 2.1.277）usage 含 `input_tokens` 族字段 → 返回现代表；仅含 `prompt_tokens` 族 → 返回 legacy 表；两者皆非 → None（fail-explicit 兜底保持）；版本精确键仅作例外覆盖，既有 2.1.191/220/233/259/legacy 五键解析结果逐字节不变"
  - "A2 legacy 不误判：legacy 分支（2.1.191 与 'legacy' 键）仍返回 prompt_tokens 族表；探测路径对只含 prompt_tokens 的 usage dict 返回 legacy 表、对只含 input_tokens 的返回现代表，不出现交叉误判"
  - "A3 session_id join 键：`cli/usage_ledger.py` 的 `CliCallRecord` 增加 `session_id: str | None` 字段；`cli/wake_backend.py` 在 wake 拿到 `WakeResult.session_id` 后写 `.map/usage/runtime-sessions/<persona>.json` 指针（`{session_id, updated_at}`）；`record_cli_call` 按 persona 读取指针文件，命中则记账带 session_id，缺失/损坏记 null 不抛错"
  - "A4 会话硬上限：`cli/waker_state.py` persona_state 增加 `wake_count`（默认 0）；`cli/simple_waker.py` 每次成功 wake 后 +1，达到阈值（默认 300，env `MAP_WAKER_SESSION_MAX_WAKES` 可覆盖，非法值回退默认）在下次 wake 前走既有 `backend.reset_session()` 路径（`clear_runtime_session_state` + 计数归零）；与既有 contract/topics 重置触发器并存不冲突"
  - "A5 测试钉住：新建 `tests/test_cost_ledger_field_probe.py`（或就近扩展）覆盖：未知现代版本解析 4 字段 / legacy 键与 legacy 探测 / 交叉字段不误判 / 全未知兜底 unknown；`tests/test_usage_ledger*` 覆盖 session_id 记账（有指针/无指针/损坏 JSON）；`tests/test_simple_waker*` 覆盖上限触发 reset + 计数归零 + env 覆盖；既有 layer2/ledger/waker 测试零回归"
  - "A6 文档：`docs/MAP-USAGE-LEDGER.md` 增补两源 join 方法（ledger.session_id × runtime jsonl session_id）与轮次上限说明（含 120K 上下文实时检测不做的局限声明）"
  - "A7 验收：`ruff check` 通过；`pytest tests/ -q` 全绿（基线以开实验时 HEAD 为准，只增不减，0 failed）；`git diff --name-only` 落在窄白名单 `^cli/`、`^tests/`、`^docs/`"
  - "A8 真实数据验证：对本机 `.map/usage/cli-calls.jsonl` 跑 `map usage summary --cost`，known（非 unknown 版本分支）占比 > 95%（修复前实测 unknown 占比约 22%）；边界显式不做：网关 cache_control 修复（环境动作）、120K 实时上下文检测、SKILL.md 变更（无契约升版）"
evidence_keys:
  - "pytest_summary:新增字段探测/session_id 记账/上限触发用例全绿，全量 pytest -q 0 failed"
  - "实测输出:2.1.277 样例 usage 经 _resolve_field_map 返回现代 4 字段；.map/usage summary --cost known 占比 > 95%；wake_count 达阈值日志显示 reset_session"
  - "grep 核证:layer2_mapper.py 无纯版本键控兜底（探测路径存在）；usage_ledger.py CliCallRecord 含 session_id 字段；simple_waker.py wake 分支含阈值检查"
dependencies:
  - "话题 runtime-token-cache-and-measurement Round 1：participant 实测（cache 命中率 0%、单 session 2315 轮 252M input、ledger unknown 占比 22%）与 host round1 表态"
  - "阶段一实验 4e4206de 已完成的 I7 出口记账（cli/usage_ledger.py、cli/cost_ledger/layer1_collector.py、map usage summary）"
  - "layer2_mapper.py docstring 预埋的根治方向（现代 schema 默认表 + 例外表，须实验流程确认——本实验即该流程）"
  - "wake_backend.WakeResult.session_id 与 persona_state.runtime_session_id / clear_runtime_session_state 既有机制"
  - "既有 reset_session 路径（disconnect + clear_runtime_session_state，simple_waker contract/topics 触发器复用中）"
---

# 阶段二：runtime 度量修复（join 键 + 字段映射 + 会话上限）

## 背景

阶段一（4e4206de，已完成）交付了 CLI 出口记账（I7），但 participant 在话题
`runtime-token-cache-and-measurement` 的实测暴露三个 MAP 侧缺口：

1. `layer2_mapper.VERSION_FIELD_MAP` 精确版本键控：新 SDK 版本（如实测 2.1.277）
   落入 unknown 分支，4 字段全 unknown、成本视图缺失——ledger 实测 unknown 占比 22%
2. `CliCallRecord` 无 `session_id`：MAP ledger 与 runtime 侧探针
   （`docs/probes/token-cost-audit.py` 按 session_id 聚合）两源 join 不上
3. simple-waker 无会话轮次上限：单 session 累积 2315 轮 / 252M input 全价计费
   （cache 失效下成本集中点）

范围界定（重要）：**网关 `cache_control` 修复不属于本实验**——该链路在
runtime 与模型 API 之间，是环境/infra 动作，MAP 代码库不可达（AGENTS.md 边界）。
本实验只做 MAP 侧度量与成本控制。

## 改动清单

- A1 `cli/cost_ledger/layer2_mapper.py`：改为「现代 schema 默认表 + legacy 例外表
  + 字段名探测」。探测规则：usage 含 `input_tokens` 族字段 → 现代表；仅含
  `prompt_tokens` 族 → legacy 表；版本精确键仅作例外覆盖。fail-explicit 边界保持：
  探测确定性、不静默猜测，两者皆非才 unknown。
- A2 `cli/usage_ledger.py` + `cli/wake_backend.py`：`CliCallRecord` 增加
  `session_id: str | None`；waker 在 wake 拿到 `WakeResult.session_id` 后写
  `.map/usage/runtime-sessions/<persona>.json` 指针；CLI 记账时按 persona 读取。
- A3 `cli/simple_waker.py` + `cli/waker_state.py`：persona_state 增加 `wake_count`，
  每次成功 wake +1；达到阈值（默认 300，`MAP_WAKER_SESSION_MAX_WAKES` 可覆盖）
  在下次 wake 前走既有 `reset_session()` 路径并归零。120K 上下文实时检测不做
  （runtime jsonl 为事后审计，轮次上限作为近似代理，局限记入文档）。
- A4 测试：探测规则（现代/legacy/未知版本）、session_id 记账、上限触发 reset。
- A5 文档：`docs/MAP-USAGE-LEDGER.md` 增补两源 join 方法与轮次上限说明。

## 风险

- 字段探测对「字段名相同但语义变化」的未来版本会误判——按现行 schema 无此情况，
  版本精确例外表保留为覆盖逃生口
- session 指针文件为 per-persona 单写者（waker 串行），并发风险低
