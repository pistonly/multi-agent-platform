# Round 2 · M54B 短 ID 解析（E2 修复）

**日期**: 2026-08-15
**阶段**: running → 继续（M54A 已于 Round 1 交付）
**范围**: `--id` 接受 ≥8 位 hex 前缀，服务端 DB 层解析（plan v2 r1 评审共识）

## 交付内容（四层贯通）

| 层 | 文件 | 改动 |
|----|------|------|
| service | `server/services/project_service.py` | `list_experiments` 新增 `id_prefix`，DB 层 `CAST(Experiment.id AS CHAR) LIKE '<prefix>%'`（不整表拉取） |
| API | `server/api/experiments.py` | list 端点新增 `id_prefix` Query（8..32 hex，pattern 校验）透传 svc |
| SDK | `sdk/python/map_client/client.py` | `list_experiments_page(id_prefix=...)` 归一大小写/连字符后透传 |
| CLI | `cli/shortid.py`（新）+ `cli/commands/experiment.py` + `cli/main.py` | 通用 `resolve_ref` / `normalize_uuid_like`；22 个 experiment 命令 `--id` 放宽为 str 并经 `_rid()` 解析；`_run` 接受 str 并离线规范化（escalation 不受短前缀影响） |

## 解析规则（`cli/shortid.py`）

- 36 字符 UUID / 32-hex → 直通，**不**触发 list 查询
- 8..31 hex → `list_experiments_page(id_prefix=…, include_archived=True, page_size=50)`；唯一命中解析为完整 UUID；无命中 usage error（exit 2）；多命中列候选（12-hex 短形 + 标题，最多 5 个）并提示加长前缀
- 其他（<8 hex / 非 hex）→ usage error（exit 2）
- 大小写、连字符归一（`F4EF8CB2` 与 `f4ef8cb2` 等价）

## Evidence（对照 plan evidence_keys）

1. **[第 3 项 · 字面量兑现]** `map --persona host experiment show --id f4ef8cb2 --format json` →
   `"ok": true, "data": { "id": "f4ef8cb2-a9af-46d1-9259-ca728fd33428", "title": "M54 机器可读输出贯通", "phase": "running", ... }`
   —— 8 位短前缀解析为完整 UUID，且与 M54A 的子命令级 `--format json` 组合可用（证据链请求：`GET /experiments?id_prefix=f4ef8cb2` → `GET /experiments/f4ef8cb2-a9af-46d1-9259-ca728fd33428`）。
2. 无命中边界：`experiment show --id 00000000` → `Error: Invalid value: no experiment matches id prefix '00000000'; check the id or list experiments first`（exit 2，usage error 语义）
3. 大写归一：`experiment status --id F4EF8CB2` → 正常输出 `actions: ['complete']`
4. help 可见：`experiment log --help` → `--id TEXT  Experiment UUID or >=8-hex-digit prefix (v0.12 M54B)`
5. 测试：新增 `tests/test_cli_shortid.py` 15 用例（单元 8 + 集成 7）；全量 `pytest -q` **430 passed / 2 skipped**（Round 1 为 415），`ruff check server cli sdk scripts tests` 清零

## 偏差与修复记录

- **真机首验失败（旧进程）**：首次验证 `--id f4ef8cb2` 报歧义且候选不以该前缀开头——本地 uvicorn 是旧进程，未加载 `id_prefix` 代码，未知 query 参数被 FastAPI 忽略后返回全表首页。重启 server 后通过。教训：涉及服务端改动的真机验证必须先确认进程加载了新代码。
- **随机 UUID 歧义对构造**：两个随机 UUID v4 共享 8-hex 前缀的概率 ≈ 16^-8，测试中的歧义用例必须显式构造共享前缀的 UUID 对（首轮 1 skip 后修正）。
- **`_run` escalation 兼容**：命令层现在向 `_run` 传 str（可能是短前缀）；`_run` 入口用 `normalize_uuid_like` 离线规范化——完整 UUID 字符串照常触发 escalation 查询，短前缀退化为 None（跳过 escalation，hint/recovery_command 仍完整输出）。

## 剩余

- M54C（JSON 输出结构对齐 + schema 文档）待启动
- 实验收尾（complete → result_review → accept-result）待 M54C 完成后进行
