# 执行日志 r2（host）：M62-b/c 拆除完成

## 实施内容（时序门复核通过后执行）

**M62-b 后端**：
- API：`server/api/feedback.py` 4 端点重写为 410 + `feedback_retired` 引导 body（含两行分流 + 只读保留提示）；router 挂载保留（410 需要路由）
- SDK：`client.py` 5 方法删除（删除位点留注释）；pydantic DTO `map_types/schemas/feedback.py` 删除，两处 `__init__.py` re-export 清理（ruff 顺手清 5 个 unused import）
- CLI：`feedback.py` 4 命令 stub 化（exit 2 + `_HINT` 两行分流）；保留原参数签名（shell 兼容），`--help` 正常

**M62-b Web**：FeedbackPage.tsx 删除、App.tsx lazy import + 路由删除、api/client.ts 类型 import + 三 fetcher 删除、client.test.ts feedback describe 块删除、types.ts 三别名删除、App.lazy.test.ts feedback 用例删除、Layout.tsx「反馈」导航删除、`types.generated.ts` regen（源头 schema 删除后零 Feedback 类型）

**M62-c Skill 三层**：`.cursor/skills/`（platform-feedback.md 删除 + SKILL.md/bootstrap-troubleshooting.md/host-checklist.md 引用改退役引导）→ `sync-bundled-skills.sh` 同步 → 四个 `.map/claude-runtime-home*/` 副本整体重装；README/QUICKSTART 无残留

**测试**：test_feedback.py / test_feedback_admin_cli.py 整文件删；dry-run 分类：`_WRITE_COMMANDS` 移除 feedback submit/update、`_READ_ONLY_COMMANDS` 登记四命令（exit 2 非 API 写）、parametrize 移除两条写断言；M55 错误信封测试的 422 场景从 feedback 端点迁移至 `POST /agents/me/inbound-events`（feedback 410 后原场景不可造）；新 `test_feedback_stub.py`（5 用例：CLI stub exit 2/文案分流/410 body/ORM+数据只读）**显式入 `_FAST_GATE_MODULES`**

**边界执行发现（超出 plan 预列但属同一定稿口径）**：
- `server/services/platform_feedback_service.py` 零引用死代码 → 删除（service 层属入口非历史层）
- `map_types/enums.py` FeedbackCategory/FeedbackStatus 保留——ORM Enum 列依赖（只读历史层）
- CHANGELOG 文件不存在（仓库无此惯例）→ breaking 记录以 PRD v0.15 §SDK 为载体（reviewer 意图保持）
- 踩坑：stub 首版用 `object` 类型注解导致 typer 整链 `RuntimeError: Type not yet supported`（`map skill --help` 同炸），改 `str | None` 修复——lazy 命令注册在 import 期执行，一个坏注解炸整个 CLI

## 真机验证证据

- CLI：`map feedback --help` 正常列四命令；`feedback list` 实测 exit 2 + 分流文案（含 GitHub issue / MAP topic / read-only 三要素）
- API：TestClient 四端点（POST/GET×2/PATCH）410 + `feedback_retired` + hint 断言（test_feedback_stub）
- 防半拆 grep（participant 口径 `cli/ server/api/ sdk/python/map_client/`）：仅剩 main.py/router.py 的 stub 注册、retired 文案、删除位注释——无功能入口残留
- Web：`npm run build`（tsc + vite）通过；vitest 全量 **16 files / 136 tests passed**（含 lazy/route 删除后无死链）
- 后端：fast-gate 全量 **592 passed**（新增 test_feedback_stub 5 用例在白名单内执行；1092 deselected）
- ruff：本次触及 .py 文件零错误（存量 session_wake_log.py UP038 非本次范围）
- 清账快照维持 `[('resolved', 11)]`（时序门证据链完整）

## PRD

`docs/prd/v0.15.md` 新建：定稿依据（争议 1 四条证据 + participant 补强）、时序硬约束执行记录、拆除面清单（只读历史层白名单）、SDK breaking 声明、防半拆验收口径。
