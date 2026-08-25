## 实施 log

### I0 现状盘点

- server 侧 project 创建唯一性：`server/services/bootstrap_service.py::run_bootstrap` 仅校验 `project_key` 全局唯一 + agent name 唯一，**无** `workspace_path + content_root` 联合键校验（A5 待补缺口）；`fs_source_service` 只有 `_ensure_content_root_matches`（单 project 内 content_root 一致性），无跨 project 归属校验。
- `map bootstrap` / `map auth reissue` 现状：`bootstrap_project_map` 遇已存在 key 即 409（无 `--heal`）；`reissue_map_token` 只写回 `agents.local.yaml`（无 `--rewrite-config`）。与 plan 背景一致。
- 基线收集：`.map/config.yaml` 的 `project_id` 目前与 server 权威一致（`map doctor config` 无 project_id 分叉），但 **authority agents 与本地 agents.yaml 存在真实分叉**（本地缺 `map-agent` / `map-agent-2`，后续 I5/A2 heal 与文档处置可覆盖）。

### I1（A1）`map doctor --config` 对账 + 告警钩子 + `--check` 码表

- **新增 `cli/commands/doctor.py`**：`inspect_config_divergences` 读 `.map/config.yaml` + `agents.yaml` 与 server 权威对比，产出分叉项（config 类：project_id 陈旧 / 缺 project_id / key 未注册；agent 类：缺权威/多出未注册），逐项给修复命令；`map doctor config` 人类可读清单，`--check` 按 A1 码表退出。
- **告警钩子**：`persona whoami` 与 `fs status`（human/yaml 输出时）接入 `warn_config_divergence`——分叉时 stderr 单行 `[WARN] config 与服务端权威存在 N 处…（首个建议: …）`，静默通过不打断正常输出。
- **单测锚点**：`tests/test_doctor_config.py` 14 用例——clean / project_id 陈旧 / 缺 project_id / key 未注册 404 / agent 缺+多 / 缺 config / 网络错 / HTTP 500 / agent 查询错各分支 + `--check` 0/1/2 码表（CliRunner 锚退出码）+ 告警钩子只在分叉时出声。
- **dry-run 分类登记**：`map doctor config` 归入只读命令（test_dry_run_write_commands.py）。
- **live 实测（2026-08-25）**：
  - `map doctor config` → 「发现 1 处分叉：- [agent] 本地 agents.yaml 缺权威 agent: map-agent, map-agent-2 / 修复建议: map auth reissue …」
  - `map doctor config --check` → `doctor config: diverged (1)`，rc=1
  - `map doctor config --check --project-root /tmp/doctor-emptydir` → `doctor config: diagnostic-error (2)`，rc=2（缺 .map/ 配置）
  - 码表 0 由单测 test_clean_when_local_matches_authority 锚定（live 无 project_id/key 分叉，agent 分叉为真实存量差异）
  - `map --persona host persona whoami` / `fs status` 均输出 `[WARN] …1 处分叉…` 告警行
- **commit**：`d32df89 map exp 3b7c2b44 I1(A1): map doctor --config 分叉对账 + whoami/fs status 告警 + --check 码表 0/1/2`（仅 I1 相关 6 文件，窄提交）

### I2（A2）`bootstrap --heal` 非破坏性修复 + token 不变机器断言

- **SDK `heal_project_map_config`**（sdk/python/map_client/bootstrap.py）：key 已存在 → 不 create / 不 reissue，只回写两处——`config.yaml` 的 `project_id` = 按 key 解析到的权威 id；`agents.yaml` persona 的 `agent_name` 若未在项目权威注册且按 `{slug}-{persona}` 约定命中权威 agent 则回写（无法确定性匹配的保留，交由 `map doctor --config` 继续列分叉）。鉴权解析：显式 token → `MAP_ADMIN_TOKEN` → agents.local.yaml 存活 token；无 token 报 ValueError 引导先 reissue。
- **CLI `map bootstrap --heal`**：输出 config/agents 回写明细 + 「agents.local.yaml token: 未触碰」声明。
- **机器断言测试**（tests/test_bootstrap_heal.py 6 例）：heal 前后 `agents.local.yaml` **字节不变**；MockTransport 记录请求方法，断言仅 GET（`["GET","GET"]`，零 create/reissue 写请求）→ server 端 token 亦不变；stale project_id 回写 / stale agent_name 约定修复 / clean 无改动 / 无 token / 缺 config / key 未注册 404 各分支。
- **live 实测（2026-08-25）**：`map bootstrap --heal --key multi-agents-platform` → `project_id=106216a7…`、「config.yaml project_id: 与权威一致，无需改动」「agents.yaml agent_name: 无需改动」，rc=0；`diff .map/agents.local.yaml` 前后 → **UNCHANGED**（字节一级验证）。
- **commit**：`50e2c89 map exp 3b7c2b44 I2(A2): map bootstrap --heal 非破坏性修复 + token 不变机器断言`

### I3（A3+A4）bootstrap 409 意图分流 + `auth reissue --rewrite-config` 边界

- **A3 分流文案**：新增 `bootstrap_conflict_triage(project_key)`（sdk/python/map_client/bootstrap.py）——按用户意图三条出路：丢 token → `map auth reissue`；config 陈旧 → `map bootstrap --heal`；想整体重做 → archive/换 key 重建。两条触发路径共用：本地 `agents.local.yaml` 已存在的 `ValueError`（原「Remove or --force」文案替换）与 CLI 对 server 自服务 409 的 catch（cli/main.py `MAPHTTPError.status_code==409` 追加分流）。
- **A4 `--rewrite-config`**（cli/commands/auth.py）：`auth reissue` 新增 `--rewrite-config` flag，**默认关闭**——仅显式开启时 reissue 成功后顺带 `heal_project_map_config` 回写 `config.yaml` 的 `project_id`，heal 失败仅 `[WARN]` 不阻断（reissue 本身已成功）。默认保持 token 丢失恢复语义、不碰 config（回归测试钉住，避免误用 reissue 吊销/改写）。
- **测试**：`test_bootstrap_conflict_triage.py` 3 例（三出路文案 / 本地已存在 ValueError 含分流 / CLI server 409 输出分流）+ `test_auth_reissue.py` 2 例（默认不修 config 字节不变回归 / `--rewrite-config` 时 heal 恰好一次并输出「project_id 已回写」）。相关 30 例全绿，ruff 全绿。
- **live 实测（2026-08-25）**：`map bootstrap --key multi-agents-platform` → `已存在（或 .map/ 已初始化）。按意图分流：…reissue … --heal … archive…` 三出路齐全；`map auth reissue --help` → `--rewrite-config` 选项可见。
- **commit**：`a1a869a map exp 3b7c2b44 I3(A3+A4): bootstrap 409 分流文案 + auth reissue --rewrite-config 边界`
### I4（A5）workspace_path+content_root 联合键唯一 + doctor 双归属兜底

- **共享 helper**（server/services/_lookups.py）：新增 `ensure_workspace_unique(db, *, workspace_path, content_root, exclude_project_id=None)`——命中已有 project 即抛 `ConflictError(409)` 且 detail 指明「已被 project '<name>' (key=<key>) 认领」，并指向 `docs/WORKSPACE-UNIQUENESS.md` 处置指引；`exclude_project_id` 支持 update 排除自身。
- **三处接入**：`project_service.create_project`（project_key 检查后）、`project_service.update_project`（仅当 payload 涉及 workspace_path/content_root 字段时，排除自身）、`bootstrap_service.run_bootstrap`（project_key/agent name 检查后）。同一 repo 不同 content_root 开多 project 不被误伤。
- **doctor 双归属兜底**（cli/commands/doctor.py `_workspace_duplicates`）：`inspect_config_divergences` 末尾调用 `c.list_projects(include_archived=False)`，按 `(workspace_path, content_root)` 分组，同组 ≥2 个认领者 → 追加 `("workspace", …双归属…列全署名)` divergence（`--check` 归入 1）；list 失败**软跳过**（返回 []，不破坏码表语义；非 admin 的 list_projects 仅见自己 project，跨 project 双归属需 admin 可见性，硬约束仍靠 create/bootstrap 409）。`_fix_hint` 增 workspace 类别 → 指向处置文档。
- **处置文档**：`docs/WORKSPACE-UNIQUENESS.md`——约束规则（create/register/update 409、content_root 区分合法多 project）+ 存量双归属两条标准路径（retired-surface 清理 / archive 换主）+ 验证（doctor clean(0)/diverged(1)）。
- **测试**：`tests/test_workspace_unique.py` 5 例（create 同键 409+指明归属 / 不同 content_root 201 / bootstrap 同 workspace 409 / update 排除自身 200 / update 改到已占用 409）；`tests/test_doctor_config.py` 增 4 例（双归属 workspace divergence 列全认领者、不同 content_root 不误报、单一 project clean、list 失败软跳过）。相关 51 例全绿（bootstrap/projects/heal/triage/doctor/workspace），ruff 全绿。
- **live 实测（2026-08-25）**：`map bootstrap --key a5-live-dup … --path <repo根>`（临时 project-root）→ **Error 409: workspace_path+content_root 已被 project 'Multi Agents Platform' (key=multi-agents-platform) 认领；…见 docs/WORKSPACE-UNIQUENESS.md。** 且系统 cat 到 A3 分流文案（三出路齐全）；`map doctor config --check` → `diverged (1)` 仅报预存 agent 分叉（无 workspace 误报）；daemon 已带真实 DB URL 重启。
- **commit**：`e95fec2 map exp 3b7c2b44 I4(A5): workspace_path+content_root 联合键唯一 + doctor 双归属兜底`（7 文件窄提交）
### I5（A6 + 收尾）P4-1/P3-1/P2-1 + 整仓复跑

- **P4-1 help snapshot**（cli/commands/topic.py + tests/test_topic_create_help.py）：`topic create --title` 加 `show_default=False` + help 文本——修复 typer 0.16（PATH `map` 富文本路径）对 `Option(..., "--title")` 把 Ellipsis 默认渲染成 `<object object at 0x…>` 的泄漏；snapshot 断言 3 例：无对象地址/占位符、`--title` 带 help、`--slug` 文档保留。`.venv`(0.27) 与 miniconda(0.16) 两环境输出一致（`Map topic title. [required]`）。
- **P3-1 version 对照范围**（cli/commands/version.py + tests/test_version_command.py）：新增 `map version info`（主 app 双段式，与 doctor 同构）——human 一行 `map <semver>` + 关键命令集（fs/topic/bootstrap/review）→ bundled skills 版本对照；`--json` 结构化为 `{"cli": {version, source}, "scope": {commands, skills, note}}`，note 声明「不建第二张全量漂移表，全量看 map skill list --installed」。测试锚定 4 例：semver / json 含四命令 / skills 有 bundled_version / 与 `map --version` 同源。dry-run 登记 `("version","info")`。
- **P2-1 archive 规划**（docs/PROJECT-ARCHIVE-PLAN.md，纯文档不落地代码）：project 级 archive 规划语义——dormant（不物理删、workspace 退出扫描）、dry-run 列受影响 workspace/topics、unarchive 无损（uuid5 id 稳定 + 仅切扫描可见性）；明确「不自动割让 workspace」与「本实验不落代码」，避免与 retired-surface 清理重叠改动面。
- **整仓 ruff 复跑**：`.venv/bin/ruff check .` ——预存 3 个 I001（tests/test_049_project_fs_content_config.py / test_review_archived_metadata.py / test_review_item_state_closed_migration.py，均早于本实验、非本实验改动文件）经 scoped `--fix` 清零后 **All checks passed!**；commit `7ce6ac6`。
- **commits**：`1bf813c map exp 3b7c2b44 I5(A6): P4-1/P3-1/P2-1 随手修 + 规划文档`（8 文件）+ `7ce6ac6 chore(3b7c2b44 I5): 清零 3 个预存 ruff I001（import 排序）整仓全绿` + `fb06923 chore(3b7c2b44 I5): 修复 fast suite 3 红——补 CLI 快照 + a2a 跨项目 workspace 区分`
- **fast suite**（scripts/test-fast.sh 同款：`python3 -m pytest -m "not slow and not integration and not claude_cli"`）：
  - 首轮全量：**1468 passed, 3 failed, 1 skipped, 359 deselected**（855s）。3 个失败均**自本实验引入、非预存**，逐一定因并修复：
    1. `test_compat.py::test_cli_commands_directory_inventory`——`EXPECTED_SUBAPP_FILES` 自 doctor.py（本实验 A1 新 sub-app）就漏登记一直红；补 `doctor.py` + `version.py`（A6 P3-1 新 sub-app）。
    2. `test_a2a.py::TestAgentCards::test_card_cross_project_forbidden` / `TestTaskProjection::test_tasks_cross_project_forbidden`——两测试用**同一** `workspace_path=/tmp/a2a-demo` 建第二个 project，触发 A5 联合键唯一性 409（此前无约束时 201 通过）。修法：`_bootstrap` 增 `workspace` 覆盖参数，a2a-one/a2a-two 各用独立 workspace，跨项目 403 断言意图不变。分模块复跑 `test_compat.py + test_a2a.py` → **24 passed**，ruff 对新改两文件全绿。
  - **终轮全量复跑（以实际复跑为准，同上命令重跑全仓）**：**1471 passed, 1 skipped, 359 deselected, 0 failed**（869s，exit code 0）。达成 plan I5「0 errors / 0 failed」。

## 风险

- A5 唯一性硬约束可能误伤「同一 repo 多 project」——以联合键（workspace_path + content_root）缓解，不同 content_root 开多 project 不受影响；存量「同 workspace 多 project 双归属」不自动迁移、不阻断，走处置文档（docs/WORKSPACE-UNIQUENESS.md）两条标准路径（retired-surface 清理 / archive 换主）。
- A4 `--rewrite-config` 默认关闭，仅显式开启才回写 config——避免「误用 reissue 吊销/改写」陷阱；默认语义由回归测试钉住。
- fast suite 首轮 3 红（CLI 快照 doctor.py/version.py 漏登记 + a2a 跨项目同 workspace 撞 A5 约束）均**本实验引入**，已逐一定因修复清零；终轮 1471 passed / 0 failed 为同一命令的权威全量复跑，非推算。

## acceptance

- A1 ✅ doctor --config 分叉对账 + whoami/fs status 告警 + --check 码表 0/1/2 进单测锚点；live 实测 diverged(1)/diagnostic-error(2) 两码。
- A2 ✅ bootstrap --heal 非破坏性回写；agents.local.yaml 前后**字节不变** + 请求仅 GET（token 不变机器断言）；live diff UNCHANGED。
- A3 ✅ bootstrap 已存在 key 三出路分流（reissue / --heal / archive 重建）live 实测三出路齐全，CLI 与 server 两条触发路径同盖。
- A4 ✅ auth reissue --rewrite-config 默认不修 config 回归 + 显式开启时 heal 恰好一次。
- A5 ✅ 联合键唯一（create/register/update 409 指明认领者）+ 不同 content_root 合法 201 + doctor 双归属兜底列全认领者 + 处置文档；「新建双归属被拒」+「存量双归属被列清单」两端同盖。
- A6 ✅ P4-1 help snapshot 两版本一致无 object 泄漏；P3-1 map version info --json 四命令集对照范围不建第二张全量漂移表；P2-1 archive 规划语义（dormant/dry-run/unarchive 无损）。
- 测试面 ✅ 本实验新增单测全绿；`ruff check` 整仓 **All checks passed**；fast suite 终轮 **1471 passed, 1 skipped, 0 failed**（869s EXIT=0，以实际复跑为准）。
