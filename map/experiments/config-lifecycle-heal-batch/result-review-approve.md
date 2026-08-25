# config 生命周期修复（3b7c2b44）— 结果审批（accept，返工前全量独立核证）

## 结论

**通过**。对照 plan v2（我上轮评审 4 条 reasonable / 0 条 unreasonable 放行）验收 6 项 A1-A6 全部落地且可独立复跑验证；此前 v1 指出的 2 条 unreasonable（A1 判定口径+码表、A5 存量处置）在 v2 定稿并真实执行，无回归。收口 commit 序列完整（`d32df89` I1 → `f2acc42` I5 日志补全），所有实现/测试/文档文件均已在 HEAD（`f2acc42`）。

## 独立核证矩阵（reviewer 现跑，非转述日志）

| 维度 | 判定 | reviewer 独立证据 |
|------|------|------|
| A1 码表 0/1/2 | ✅ | live 复测：`doctor config --check` → `diverged (1)` rc=1（实际代理缺权威 agent 分叉）；`--project-root /tmp/空目录` → `diagnostic-error (2)` rc=2；码表 0 由单测锚定；`doctor config` 人类清单逐项给修复命令 |
| A1 告警钩子 | ✅ | `persona whoami` / `fs status` 输出 `[WARN] config 与服务端权威存在 N 处分叉` 单行（live 可见；本站点 1 处为**真实存量差异**——agents.yaml 缺 map-agent/map-agent-2，非回归） |
| A2 `--heal` 非破坏 | ✅ | `test_bootstrap_heal.py` 6 例（MockTransport 断言仅 GET、`agents.local.yaml` heal 前后**字节不变**）；live 边界：无 `.map/config.yaml` 时拒绝（`heal 不负责首次创建`，rc=1）语义正确 |
| A3 409 意图分流 | ✅ | live 复测命中三出路（丢 token→reissue / config 陈旧→heal / 整体重做→archive）文案齐全 |
| A4 `--rewrite-config` 默认关 | ✅ | `test_auth_reissue.py` 2 例：默认不修 config 字节不变回归 + 显式开启时 heal 恰好一次 |
| A5 联合键唯一 | ✅ | live 复测 `bootstrap` 同 workspace 新建 → **Error 409** 指明「已被 project 'Multi Agents Platform' 认领」+ 处置指引（不同 content_root / retired-surface / archive 换主 + `docs/WORKSPACE-UNIQUENESS.md`）；create/update/bootstrap 三处接入 + `exclude_project_id` update 排除自身 |
| A5 存量双归属兜底 | ✅ | `test_doctor_config.py` 增 4 例（双归属 list 列全认领者 / 不同 content_root 不误报 / 单一 clean / list 失败软跳过）；`_workspace_duplicates` 归 `--check` 到 1 |
| A6 P4-1 help snapshot | ✅ | live `topic create --help` **0 处 object 地址泄漏**；`.venv(0.27)`/miniconda(0.16) 两环境一致锚点 |
| A6 P3-1 version 对照 | ✅ | live `map version info --json` → `cli.version=0.9.0` + 四命令集（fs/topic/bootstrap/review）skills 对照，note 声明不建第二张全量漂移表 |
| A6 P2-1 archive 规划 | ✅ | `docs/PROJECT-ARCHIVE-PLAN.md` @HEAD（dormant/dry-run/unarchive 无损，纯规划不落码） |
| 测试面 | ✅ | 本实验新增 7 文件核心集 **46 passed**（doctor/heal/triage/reissue/workspace/topic-help/version）+ 首轮 3 红修复面 **test_compat.py + test_a2a.py 24 passed**，均 reviewer 现跑 |
| ruff 整仓 | ✅ | `.venv/bin/ruff check .` → **All checks passed!**（reviewer 独立复跑） |
| 收口完整性 | ✅ | `git show HEAD:...` 确认 doctor.py / version.py / WORKSPACE-UNIQUENESS.md / PROJECT-ARCHIVE-PLAN.md + 5 个核心测试文件全部已进 HEAD |

## 执行过程核验（对照 log-execution.md 与 commit 序列）

- I0 现状盘点诚实：唯一性校验缺口的证据（bootstrap_service/fs_source_service 现状代码）交代清楚；基线如实承认「agent 分叉为真实存量差异」、无 project_id 分叉——不虚报。
- I5 fast suite 首轮 **3 failed 全为本实验引入**且**如实逐一定因**（CLI 快照 `EXPECTED_SUBAPP_FILES` 漏登记 doctor.py/version.py + a2a 跨项目撞 A5 约束改独立 workspace），非推卸给预存；终轮 1471 passed / 0 failed（869s EXIT=0）为同一命令权威全量复跑，`complete-metadata.yaml` pytest_summary 与日志一致。
- 收口 commit 分层窄提交（I1-I5 各一 + 日志/清零/修复分开），无混入无关改动；3 个预存 ruff I001 清零 commit `7ce6ac6` 为 scoped `--fix`（import 排序），非本实验文件，独立复跑整仓仍绿。

## 遗留（非阻塞）

- 本站点 agents.yaml 缺 map-agent/map-agent-2 为真实存量差异，`doctor config` 持续告警属**预期行为**（A1 目标），建议项目自行按指引 `reissue` 收敛；不影响本实验验收。
- 全量 fast 1471 passed 以 host 实测落档为准（reviewer 独立复跑本实验相关 70 例 + ruff 全绿），与本实验新增用例零失败口径一致。
