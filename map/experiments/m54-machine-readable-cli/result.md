# M54 机器可读输出贯通 · 实验结果

**实验**: `m54-machine-readable-cli`（`f4ef8cb2-a9af-46d1-9259-ca728fd33428`）
**周期**: 2026-08-15（Round 0–3）
**结论**: **全部 6 条 acceptance 达成**，v0.12 机器可读输出三子项（A/B/C）交付完毕

## 交付总览

| 子项 | commit | 内容 |
|------|--------|------|
| M54A 子命令级 `--format` | `a378902` | 全部叶子命令本地 `--format` 选项，子命令级覆盖全局位；compat 测试锁定两种调用位置等价 |
| M54B 短 ID 解析 | `b9379df` | 22 个 experiment 命令 `--id` 支持 ≥8 hex 前缀；四层贯通（service `id_prefix` DB CAST-LIKE / API Query / SDK / CLI `resolve_ref`）；`_run` escalation 离线规范化兼容 |
| M54C JSON 输出契约 | `bd830a9` | `status --format json` stdout 纯净化；`pre-complete` 去内嵌 `ok`；`docs/cli-json-output.md` 权威契约 + 冻结字面量护栏测试 9 用例 |
| 泛化决策（PRD 回写） | 本次收尾提交 | `docs/prd/v0.12.md` 风险表：topic/agent 族短 ID **延后**，理由与后续接入路径已记录 |

## Acceptance 逐条核销

| # | acceptance | 状态 | 证据 |
|---|------------|------|------|
| 1 | 子命令级与全局 `--format` 等价，compat 测试锁定 | ✅ | `tests/test_compat.py` + Round 1（a378902） |
| 2 | `--id` 8 位短前缀三分支（唯一/多义列候选/无命中提示） | ✅ | `tests/test_cli_shortid.py` 15 用例；真机三分支验证（log-r2 evidence 1–3） |
| 3 | JSON 字段名与 REST/map_types 一致（uuid 36 位、ISO8601），可被 map_types 反序列化 | ✅ | `tests/test_cli_json_schema.py` 9 用例（冻结字面量 → CLI → `model_validate` 往返）；真机字面量（log-r3 evidence 1–3） |
| 4 | host 仅凭 CLI 完成查询全流程，无 SQLite 直查与 curl | ✅ | `experiment list --format json \| jq` 取短 id → `show --id f4ef8cb2` 得完整 UUID（log-r2 evidence 1 证据链） |
| 5 | helper 泛化评估：低成本则落地，否则 PRD 记录延后决策 | ✅ | **评估后延后**——topic 族 `--id` 被 M51 三态路由占用（DB UUID / FS uuid5 / slug），FS uuid5 与随机 UUID 前缀层面不可区分，插入短前缀有误路由风险；topic 已有 slug 替代、agent 按名称寻址。决策与后续接入路径已回写 `docs/prd/v0.12.md` 风险表 |
| 6 | 新增 pytest 覆盖，现有 406 测试不回归 | ✅ | 新增 24 用例（shortid 15 + json_schema 9，另有 Round 1 compat 扩充）；全量 **439 passed / 2 skipped**（基线 406 → 无回归），ruff 清零 |

## Evidence keys 核销

- `pytest 新增 m54 测试全绿`：24/24 新用例通过
- `pytest 快速门控全量回归通过`：439 passed / 2 skipped / 1130 deselected
- `CLI 实测 list --format json | jq + show --id 短id 返回完整 uuid`：见 log-r2 evidence 1、log-r3 evidence 1（`"id": "f4ef8cb2-a9af-46d1-9259-ca728fd33428"` 字面量）

## 过程发现（移交后续里程碑）

- **E8**（瘦身模式 frontmatter 门禁误伤）：Round 0 发现，评审 r2 确认修复归属 **M55**，已入 PRD 证据清单
- M55（错误信封可执行化）/ M56（命令路由统一）按 PRD 顺序待启动

## 执行记录索引

- `log-r0.md`：Round 0（E8 发现 + plan v2 修订背景）
- `log-r1.md`：M54A 交付（a378902）
- `log-r2.md`：M54B 交付（b9379df）
- `log-r3.md`：M54C 交付（bd830a9）
