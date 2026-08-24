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
