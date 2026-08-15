# Round 3 · M54C JSON 输出结构对齐 + schema 文档（E3/E4 修复）

**日期**: 2026-08-15
**阶段**: running → 继续（M54A/B 已于 Round 1/2 交付）
**范围**: `--format json` 输出契约立约——payload 走 map_types 序列化、stdout 纯净化、契约文档 + 冻结字面量护栏测试（plan 验收第 3 条）

## 交付内容

| 交付 | 文件 | 内容 |
|------|------|------|
| 结构修复 1 | `cli/commands/experiment.py` | `experiment status` json 模式跳过人类提示行（`actions: [...]` 前导文本曾使 `jq` 首行即失败）；`actions` / `blocked_on` / `phase_owner` 已在 `data` 内，无信息损失 |
| 结构修复 2 | `cli/commands/experiment.py` | `experiment pre-complete` 移除 `data.ok`（与外层信封 `ok` 语义冲突）；data 精确 4 键：`experiment_id` / `phase` / `current_plan_version` / `evidence_keys` |
| 契约文档 | `docs/cli-json-output.md`（新） | 权威输出契约：信封结构、通道与退出码、各命令 payload 形状表（对应 map_types 模型）、序列化约定、jq 配方、漂移防护与变更记录；与输入侧 `docs/cli-schemas.md` 互补 |
| 护栏测试 | `tests/test_cli_json_schema.py`（新） | 9 用例，已入 fast-gate 白名单（`tests/conftest.py`） |

## 护栏设计（防 CLI 输出与 schema 漂移）

- stub payload 为**手工冻结的 REST 字面量**（非 `model_dump` 自产自销）→ 驱动 stub transport → CliRunner → `json.loads` → `map_types.model_validate` 往返：schema 改名/改型即失败
- `test_status_json_stdout_is_pure_json` 对**整个 stdout** 做 `json.loads`（污染守卫）；配套 `test_status_default_format_keeps_human_hints` 锁定默认 yaml 输出不变（plan 风险与对策：M54C 只动 json 分支）
- 错误信封经 `CLIErrorEnvelope`（`extra="forbid"`）校验，字段集漂移即失败
- `test_documented_core_fields_exist_in_map_types` 把文档「payload 形状表」引用的字段集 pin 进 map_types 模型

## Evidence（对照 plan 验收第 3 条）

1. **[status 纯净化 · 字面量]** `map --persona host experiment status --id f4ef8cb2 --format json 2>err` → stdout 首行即 `{`，`"ok": true, "data": { "id": "f4ef8cb2-a9af-46d1-9259-ca728fd33428", "phase": "running", "actions": ["complete"], "blocked_on": "none", ... }`，stderr 0 字节；`jq -e '.ok == true and (.data.actions | index("complete") != null)'` → `true`
2. **[pre-complete 去内嵌 ok · 字面量]** `map --persona host experiment pre-complete --id f4ef8cb2 --metadata meta.yaml --format json` → `{"ok": true, "data": {"experiment_id": "f4ef8cb2-a9af-46d1-9259-ca728fd33428", "phase": "running", "current_plan_version": 2, "evidence_keys": ["api_health", "logs_doc"]}}`，stderr 0 字节
3. **[schema 往返]** `ExperimentDetailRead.model_validate(data)` / `ExperimentSummaryRead[]` / `ExperimentLogRead[]` / `LogCreateResponse` 均通过（uuid 36 位小写、ISO8601、字段名与 REST 一致——同一 pydantic 模型，无别名差异）
4. **[测试]** 新增 `tests/test_cli_json_schema.py` 9 用例全绿；全量 `pytest -q` **439 passed / 2 skipped**（Round 2 为 430），`ruff check server cli sdk scripts tests` 清零

## 偏差与修复记录

- **stub 路径匹配首跑 6 失败**：SDK 请求带 `/api/v1` 前缀，list 的 `endswith("/experiments")` 恰好命中而 get/logs 的裸路径精确匹配全落空；改为前缀无关的 `endswith` 匹配（logs 规则置于单 id 规则之前避免遮蔽）。
- **文档 datetime 示例与真机不符**：初稿示例 `2026-08-15T03:12:45Z` 带 `Z` 后缀，真机输出为 naive UTC（`2026-08-15T11:47:33`，pydantic `model_dump(mode="json")` 对 naive datetime 无时区后缀）；文档与测试正则同步修正（时区后缀改为可选，输入侧 `Z`/偏移仍接受）。
- **pre-complete 真机首跑误报**：在 `/tmp` 下运行报 `No .map/config.yaml found`——CLI 需在项目根目录（`.map/` 上下文）运行，与 M54C 改动无关；回项目根目录后通过。

## 剩余

- 实验收尾（complete → result_review → accept-result）：M54A/B/C 三子项均已交付，可进入收尾流程
- M55（错误信封）/ M56（命令路由）按 PRD 顺序待启动
