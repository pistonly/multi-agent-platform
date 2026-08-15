# M54 机器可读输出贯通 · 实验结果（规范模板版）

**实验**: `m54-machine-readable-cli`（`f4ef8cb2-a9af-46d1-9259-ca728fd33428`）
**周期**: 2026-08-15（Round 0–3）
**结论**: 全部 6 条 acceptance 达成，v0.12 机器可读输出三子项（A/B/C）交付完毕

## summary

M54 三子项全部落地：**M54A** 子命令级 `--format`（`a378902`）——所有叶子命令本地 `--format` 选项，子命令级覆盖全局位，compat 测试锁定两种调用位置等价；**M54B** 短 ID 解析（`b9379df`）——22 个 experiment 命令 `--id` 支持 ≥8 hex 前缀，四层贯通（service `id_prefix` DB CAST-LIKE / API Query / SDK / CLI `resolve_ref`），`_run` escalation 离线规范化兼容；**M54C** JSON 输出契约（`bd830a9`）——`status --format json` stdout 纯净化、`pre-complete` 去内嵌 `ok`、`docs/cli-json-output.md` 权威契约文档 + 冻结字面量护栏测试。收尾阶段完成 helper 泛化评估并将延后决策回写 PRD（`1c860e8`）。全量 **439 passed / 2 skipped**（基线 406 无回归），ruff 清零。

## 实施 log

- Round 0：E8（瘦身模式 frontmatter 门禁误伤）发现与 plan v2 修订背景 → [log-r0.md](log-r0.md)
- Round 1 · M54A 交付（`a378902`）：子命令级 `--format` + compat 测试 → [log-r1.md](log-r1.md)
- Round 2 · M54B 交付（`b9379df`）：短 ID 解析四层贯通 + 15 用例 + 真机三分支验证 → [log-r2.md](log-r2.md)
- Round 3 · M54C 交付（`bd830a9`）：JSON 契约文档 + stdout 纯净化 + 9 用例护栏 → [log-r3.md](log-r3.md)
- 收尾：泛化评估结论回写 [docs/prd/v0.12.md](../../docs/prd/v0.12.md) 风险表（`1c860e8`）

## 风险

- **topic/agent 族短 ID 延后**（已决策记录）：topic 族 `--id` 被 M51 `_resolve_topic_ref` 三态路由占用（DB UUID / FS uuid5 / slug），FS uuid5 与随机 UUID 在 8 位前缀层面不可区分，直接插入短前缀有误路由风险；topic 已有 slug 人机工学替代、agent 按名称寻址，增益有限。通用 helper（`cli/shortid.py` `resolve_ref`）已按无 experiment 依赖形态落地，后续接入仅补 server `id_prefix` + SDK 透传 + 路由分支
- **E8 移交 M55**：瘦身模式 frontmatter 门禁误伤（`--file` 上传仍按 frontmatter 校验提示），Round 0 发现、评审 r2 确认修复归属 M55，已入 PRD 证据清单
- **JSON 契约防漂移**：依赖冻结字面量护栏测试（`tests/test_cli_json_schema.py`）；新增命令时须按 `docs/cli-json-output.md` 的「漂移防护」节登记 payload 形状，否则文档与实现漂移只能在 review 兜底

## acceptance

| # | acceptance | 状态 | 证据 |
|---|------------|------|------|
| 1 | 子命令级与全局 `--format` 等价，compat 测试锁定 | ✅ | `tests/test_compat.py`（a378902） |
| 2 | `--id` 8 位短前缀三分支（唯一/多义列候选/无命中提示） | ✅ | `tests/test_cli_shortid.py` 15 用例；真机三分支（log-r2 evidence 1–3） |
| 3 | JSON 字段名与 REST/map_types 一致（uuid 36 位、ISO8601），可被 map_types 反序列化 | ✅ | `tests/test_cli_json_schema.py` 9 用例往返校验；真机字面量（log-r3 evidence 1–3） |
| 4 | host 仅凭 CLI 完成查询全流程，无 SQLite 直查与 curl | ✅ | `experiment list --format json \| jq` 取短 id → `show --id f4ef8cb2` 得完整 UUID（log-r2 evidence 1） |
| 5 | helper 泛化评估：低成本则落地，否则 PRD 记录延后决策 | ✅ | 评估后延后，决策与理由回写 `docs/prd/v0.12.md` 风险表（1c860e8） |
| 6 | 新增 pytest 覆盖，现有 406 测试不回归 | ✅ | 新增 24 用例全绿；全量 439 passed / 2 skipped，ruff 清零 |
