# M54 log

## Round 0：计划创建与评审（2026-08-15，review 阶段补记）

### 创建踩坑

- 计划落盘 `map/experiments/m54-machine-readable-cli/plan.md`（frontmatter：title / acceptance×6 / evidence_keys×3 / dependencies×2）
- 首次创建尝试 `--plan-file-path`（瘦身模式）被 422 拒绝：`STATE_MACHINE_PLAN_MARKER_MISSING`
- **根因（E8）**：瘦身模式发送 `PlanInput(file_path=...)`，`content_md` 为空；而 `project_service.create_experiment` 的 frontmatter 门禁校验的是 `payload.plan.content_md`（project_service.py:285）——瘦身路径被门禁误伤，服务端未区分两种输入形态
- 回退 `--plan-file` 全量路径创建成功：`f4ef8cb2-a9af-46d1-9259-ca728fd33428`，phase=review
- 观察：create 响应直接返回完整 UUID（E1 的痛点实际只在 list 表格输出场景）
- E8 处置：reviewer Round 1 建议纳入 v0.12 证据清单（r2），host 采纳，修复归属 M55（错误信封一并处理 file_path 形态）

### E1 复现记录（评审取数过程）

- `experiment review list --format json` 报 `No such option: --format`——`--format` 仅注册在全局 `_cli_options`，子命令级不可见
- 绕行：全局位 `map --format json experiment review list ...` 可用
- 解析障碍：响应 JSON 顶层结构未知，首次脚本解析报 `'str' object has no attribute 'get'`；递归 walk 定位到条目实际路径 `/data[0]/items`
- 结论：E1 影响 + JSON 输出路径不稳定（无 schema 文档），两者叠加使 Agent 取数成本显著升高，佐证 M54A/M54C 的必要性

### 评审流程

- reviewer Round 1：5 reasonable + 3 unreasonable（短 id DB 前缀查询 / E8 纳入清单 / log 门禁补记）
- host plan v2 修订：三条全 addressed（`--addressed-item`），plan_version=2
- reviewer v2 评审：4 reasonable、无新异议 → approve → start
