# Changelog

本文件记录面向使用者的变更（PyPI 包 `multi-agent-platform` / `multi-agent-platform-server`）。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
更早的历史见 git tag 与提交记录。

## [0.19.1] - 2026-09-25

### Fixed

- **修复 0.19.0 精简视图破坏机器可解析输出**：`map experiment show / status / log /
  complete` 在未显式指定 `--format` 时改为输出紧凑文本，而该输出不再是合法 YAML——
  `log: created id=…` 这类"键与摘要同行"的写法会让后续缩进字段变成上一行的续行，
  plan 首行是 front matter 分隔符时还会直接输出 `summary: ---`，`yaml.safe_load`
  抛 `ScannerError: mapping values are not allowed here`。**现在紧凑视图只在 stdout
  连到终端时生效**；管道 / 重定向 / 脚本继续拿到完整结构化 YAML，即 0.19.0
  声明的"机器契约不动"。直接调用方（`--format json/yaml`、`--full`）不受影响。
- **`map experiment status` 的 `phase_owner` 渲染**：Python ≥3.11 下 str-mixin 枚举
  经 f-string 会带类名前缀，输出 `phase_owner: PhaseOwner.host` 而非 `host`。

## [0.19.0] - 2026-09-24

### Added

- **`map usage summary`**：CLI 出口记账的聚合视图。每次命令出口会向
  `.map/usage/cli-calls.jsonl` 追加一行 JSONL（best-effort，失败不影响退出码），
  本命令按 persona 统计调用次数与输出字节；`--since <ISO8601>` 限定窗口，
  `--json` 输出机器可读数组 `[{persona, work_calls, total_output_bytes}]`。
- **waker 会话轮次硬上限**：单 session 默认 300 轮，达阈值先 reset session 再唤醒，
  抑制 participant 长期同话题跑数千轮导致的上下文膨胀。可用
  `--session-max-wakes` 或 `MAP_WAKER_SESSION_MAX_WAKES` 覆盖，`<= 0` 关闭。

### Changed

- **高频命令默认精简视图（省 token）**：`map work` 默认精简（实测 930B vs
  `--verbose` 4523B）、`map topic list` 默认只列 open（`--status all` 恢复全量）、
  `map experiment show / status / log / complete` 默认精简（807B vs `--full` 8671B）。
  **只影响未显式选择 format 的人类输出**——`--json` / `--yaml` 机器契约逐字段不变，
  显式 `--format` 与 `--verbose` / `--full` / `--status all` 逃生口行为不变；
  waker 与脚本侧本就显式传 `--format yaml` / `--status open`，不受影响。
- **唤醒协议改为 work-first**：`map work` 顶部的 agent 块即身份确认，
  `persona whoami` 降级为条件回退，省掉一次往返。
- **`docs/map-templates/run-map.sh.example` 清理 `PYTHONHOME` / `PYTHONPATH`**，
  避免宿主残留把 CLI 指向错误解释器。

### Fixed

- **`map topic progress` 此前漏掉全部 FS 话题待办**：`GET /agents/me/topic-progress`
  只读 DB，而 FS 事实源（`map/topics/<slug>/`）的话题恒不出现——同一时刻
  `map work` 能看到的 `round_ack` / `pending_topic_reply`，`map topic progress`
  显示为 `items: []`。FS 投影合并收敛到 `topic_progress_service` 单点，
  `GET /me/work` 与 A2A 端点不再各自手工拼接，并按 `topic_id` 去重
  （远程模式下投影行与 DB 行可能指向同一话题）。
- **话题引用接受 8 位短前缀**：`map topic list` 的 ID 列本就是 8 位前缀，
  但解析器只认 36/32 位完整 uuid，前缀被当 slug 去找 `map/topics/<8位>/` 目录、
  必然 `topic not found`。现 `--id` / `--topic` 支持完整 uuid、8..31 位 hex 前缀
  与 slug 四种形态，多命中列候选而非静默单选，未命中按 slug 片段给
  `Did you mean: <slug>`；local plane 的写命令路径（`advance-round` / `close`）
  与 `audit --target` 同步支持，不会出现「能 show 不能写」。
- **CLI 连接类错误输出收敛**：人类模式压缩到 ≤3 行并给出修复提示；`--json`
  统一走 `{error, message, hint}` 信封并 exit 2；`--debug` 仍保留 traceback。
- **`scripts/start-all-simple-wakers.sh` 在 bash 3.2（macOS）+ `set -u` 下崩溃**：
  空数组展开导致三个 persona 的 waker 完全起不来。
- **`map --cost` 账本少计 token**：`cost_ledger` 的 `layer2_mapper` 改两级路由
  （现代默认表 + 字段名探测 + 版本例外），未登记 SDK 版本不再静默落 unknown，
  真实数据 known 比例提升到 99.11%（4027 条）；同时 runtime session 指针文件
  提供 `session_id` join 键，打通 MAP ledger 与 runtime jsonl 两源对账。

## [0.18.0] - 2026-09-21

### Added

- **`map topic comment --append`**：每轮每人一个 round 文件 + 正文 immutable 约定下，
  发言后想补充内容原先无处可去——`--force` 只豁免 Summary、普通发言覆盖被拒，
  只能 advance-round（语义错位）或删文件重写（撞红线、丢 `posted_at` 审计痕迹）。
  `--append` 在本轮 `round<N>-<persona>.md` 末尾追加 `## Addendum <n> @ <utc-ts>`
  小节，原正文一字不动（immutable 仍成立），frontmatter 增记 `updated_at` 而
  `posted_at` 保留。与 `--force` / `--round-summary` 互斥（exit 2）；immutable
  报错会引导改用 `--append`，文件不存在则提示去掉 `--append`。

### Changed

- **Skill 真身迁至 `.agent/skills/`，去除对 Cursor 的目录绑定**：`.cursor` /
  `.claude` / `.codex` 下的 skills 降级为指向 `../.agent/skills/` 的符号链接
  （各 runtime 的自动发现能力保留，历史链接不断）。打包边界不变——wheel 仍只
  打包 `cli/skills`，`.agent/` 不进发行物。
- **`map skill install` 默认落点中立化**：无 flag 时按
  `.cursor → .claude → .codex → .agent` 探测已存在的厂商目录，均未命中才落到
  中性的 `.agent/skills/`；`--runtime` / `--target` 显式指定的行为不变。
- **waker 运行时契约升到 v4**：契约文件清单切到 `.agent/skills/**`。
  **部署后须重启 waker** 并核对 startup_sync 的 `skills_count=6`——旧进程持 v3
  哈希会误判 drift 并静默回写。

### Fixed

- **统一 Claude 配置入口**：跨项目可显式共用 env 文件而不混入 shell 残留账号；
  `runtime check` 只检查本地配置。
- **`map host invoke` 失败返回非零**：失败路径此前吞掉 SDK 错误，现把错误传到
  JSON 输出并以非零退出码返回。

## [0.17.0] - 2026-09-18

### Added

- **实验计划正文支持以 FS 为事实源（feature flag `plan_db_content_retired`）**：flag 未开启时
  行为不变（计划全文仍存 DB）；由 host/admin 开启（需非空 reason）后
  `map/experiments/<slug>/plan.md` 成为正文唯一事实源——`map experiment create --plan-file`
  的内联全文写入被 409 拒绝并给出自助指引，改用 `--plan-file-path` 文件引用模式；读取端统一
  收口到 `resolve_plan_content`，plan.md 缺失时 fail-closed（409）并指向 materialize，不再
  静默回落到 DB 里的旧全文。
- **`map experiment plan materialize --id <exp-uuid>`**：把 DB 中当前版计划正文物化为 FS
  `plan.md`，供存量内联实验在切 flag 前完成迁移。仅实验 creator 可调用；幂等，目标文件已存在
  且内容不同时需 `--force` 显式覆盖。
- **`map sync migrate` 覆盖 plan 域**：新增 plan 类 kind（进入 `--kinds` 登记面），`verify`
  增加 plan 域对账——flag 关闭时以信息级呈现差异，开启后为 blocking。

### Changed

- **`cryptography` 上界放开 `<50.0` → `<51.0`**（实际锁定 50.0.1）：nightly 新增的 `pip-audit`
  首次运行即报 `cryptography 49.0.0` 的 PYSEC-2026-3552 ×2，修复版本 50.0.0 恰好被原上界挡住。
  该库仅用于 `server/services/secret_encryption.py` 的 Fernet 加密，API 跨版本稳定；
  `uv.lock` 改动仅限该包（1 处版本 + hash）。
- 重生成 `web/src/api/types.generated.ts`：补上 `261d5d6`（09-14）注册的 feature flag
  `plan_db_content_retired`。该 flag 进 schema 后生成产物未同步，CI `gen-types` 自 09-14 起
  一直在红（`git diff --exit-code` 非零）。
- **`map-project-collab` 的「工作方式协商」上移到「意图路由」之前**。原顺序下 Agent 按意图路由表
  走到 persona Skill 就离开了，读不到「先与用户确认协作拓扑 A/B/C/D」这道关卡（按错误假设行动
  会空转烧 token 或死等）。同时在入口分工表与意图路由表补了指向该节的钩子行，并补上原先只有
  方式 A 有条目、B/C/D 为空的路由项（方式 C → `map host invoke`；方式 D → 回报即结束，不轮询
  `map work`）。方式 B 由「三开对话框」改为「多开对话框」（常见 2–3 个，几个不限）。
  **Skill frontmatter description 未改动**——它每次都随 skill 列表进 prompt，属于常驻成本。
- **`map experiment revise` 的去重判据在 flag 开启后改为 FS `plan.md` 字节级哈希**：flag 开启后
  DB `content_md` 可能只是指向文件的 stub，原「DB 全文等值」判据恒不成立，对同一份 plan.md 的
  重复 revise 会误 bump 版本并误归档上一版评审；改为比较 FS 文件哈希。workspace 不可达 /
  无 `plan_file_path` / plan.md 缺失（跨机部署的合法场景）时保守回退旧判据——宁可多 bump，
  不吞真实修订。flag 关闭时逐字节不变。

### Removed

- **MCP 支持整体移除**：`map-mcp` console entry、`sdk/python/map_mcp/` 模块、`[mcp]` extra、
  `Dockerfile.mcp`、docker-compose `mcp` 服务、`docker-compose.override.yml`（仅含 MCP 端口
  重映射）、`tests/test_mcp*.py` 与 `docs/MCP.md`。MCP 工具面停留在 FS 事实源迁移前的 DB
  模型（话题评论无 `file_path`/`excerpt`，无 `map work` 等），与当前协作流程脱节；且上游
  `mcp` 2.x 已将 FastMCP 改名 MCPServer，继续依赖需钉死 `<2.0`。外部项目请改用 `map` CLI
  或 Python SDK（能力等价且持续维护）。

工程与门禁（无功能变更）：

- **修 CI 覆盖率崩溃**：`pytest -n auto --cov` 缺 `--cov-branch`，子进程写 statement-only 数据、
  主进程写 branch 数据，收尾 `combine()` 抛 `DataError` 导致 backend job 三个 Python 版本全部
  INTERNALERROR（ci.yml / nightly.yml 已补 `--cov-branch`）。
- **修并行测试隔离**：`project` fixture 共用固定路径 `/tmp/test-project` 且每次 setup 清空，
  xdist 多 worker 互相踩 FS 话题目录 → `test_direct_executor_e2e` 只在全量并行下随机红。
  改为每测试独占 `tmp_path_factory` 目录。
- **模块 800 行上限**：`server/services/migration_manifest_service.py`（1209 行）拆为
  stale / lkg / execute 三个子模块，`cli/runner.py`（809 行）拆出 `cli/runner_resolve.py`；
  两者均登记进行数守卫，并新增「全仓扫描」兜底，防止新文件再逃逸白名单。
- **新增 pre-commit 配置**（`.pre-commit-config.yaml`，local hook，不进 dev extra）：ruff、
  check-deprecated、skills 双镜像 diff、mypy 受保护模块。
- 快测门禁预算按实测重设（`scripts/test-fast.sh`），`tests/PERF.md` baseline 刷新。
- `alembic.ini` 补 `path_separator = os`，消除 alembic 的 DeprecationWarning。
- 发布面元数据：Homepage/Documentation/Repository/Issues 由已 404 的 `quantaeye/...`
  改为 `pistonly/...`；classifiers 与 CI 测试矩阵加入 Python 3.13。
- Skill 清单守卫硬化：`tests/test_skill_install.py` 两处 `>= 5` 改为与真实捆绑集合精确比对，
  并新增 `TestSkillInventoryDocumentation`——校验分发给用户的 `MAP_AGENT_PROMPT.md` 里
  每个 Skill 名都出现、且「安装 N 个 Skill」的 N 等于实际数（已做漂移注入自检确认会红）。

### Fixed

- **成本账本 layer1 采集器修复「换机器即静默空采」**：`cli/cost_ledger/layer1_collector.py` 曾把
  开发机路径段 `-home-AI02-Documents-quantaeye-multi-agents-platform` 写死为 `.claude/projects/`
  下的精确目录名，换机器 / 改目录名后扫描静默返回空（本机实测 0 事件）。现提供
  `project_dir_name()` 按 `project_root` 现算编码目录名（非字母数字 → `-`），且扫描改为
  rglob 遍历 `projects/` 全部子目录兜底（runtime home 本身位于项目 `.map/` 内，无跨项目误采）。
- **layer2 映射表补 3 个实测版本**：真实数据含 SDK 2.1.220 / 2.1.233 / 2.1.259，字段名与
  2.1.191 一致；此前精确版本键控使全部真实数据落入 unknown-version 分支。已知缺陷
  （SDK 升级需手动追加映射表）已在代码注释登记，待立项根治。
- **`MAP_AGENT_PROMPT.md` 补回漏列的第 6 个 Skill `experiment-executor`**，并把「安装 5 个」改为
  「安装 6 个」。该文件是分发给外部用户 Agent 的产品面，漏掉 executor 意味着 host 用
  `--executor participant` 委派执行时，被委派方不知道有对应 Skill 可读。
- **修 4 处文档死链**：`docs/MAP-AGENT-RUNTIME-UNIFIED.md` 的 waker 文档链接多写了 `docs/` 前缀；
  `docs/prd/v0.12.md` / `v0.13.md` 的评审话题链接仍指向 `map/topics/`（话题已归档到
  `map/archive/topics/`）；`docs/prd/v0.9.md` 指向已随 legacy runtime-waker 停用的
  `MAP-RUNTIME-WAKER.md`。同时新增相对链接守卫（`tests/test_docs_consistency.py` 的
  `TestDocsRelativeLinks`），覆盖 `docs/`、两份 Skill 镜像与根级 md 共 321 条相对链接，
  含 `#锚点` 存在性校验——文档挪目录导致的静默死链此后会在 CI 变红。

## [0.16.3] - 2026-09-11

### Fixed

- CLI 写路径报错自助化：`close_note` 409 自带格式块；`ready` 态不可变报错附 `advance-round` 引导。
- host 收口已收敛话题：`discussion_converged` + 结论落 `close_note`。
- 文档与测试中的内网 LLM 网关地址改为占位域名（安全）。

### Changed

- 删除冗余的 nginx web 容器 —— 看板统一由 API 同源提供（默认端口 18400）。
- 文档订正：服务地址（看板与 API 同源）、`docs/ARCHITECTURE.md` 分层图排版。
- 归档 `status-md-v10.md` 至 `docs/archive/` 并修正 5 处引用。

## [0.16.2] - 2026-09-11

### Fixed

- Skill 分发面命令引用漂移修复（issue #1 同类泛化）：新增命令树存在性护栏，
  内省 typer 命令树校验文案里引用的 `map <group> <sub>` 真实存在。

### Added

- PyPI 发布流水线：`v*` tag 触发 Trusted Publishing，主包与 server-pkg 双包上传。

## [0.16.1] - 2026-09-11

### Fixed

- Skill 模板的 `close_reason` 与 `CLOSE_REASON_LEGAL` 对齐（issue #1），并加漂移守卫。
- CI mypy 门禁修复：补齐 mypy 依赖、消除 `strict` 全局泄漏、受保护模块清单与 CI 命令对齐，
  新增一致性守卫 `tests/test_mypy_gate_sync.py`。
- CLI 在无 `.map/` 环境不再崩溃（FS 路由降级），测试夹具 hermetic 化。

### Changed

- 重生成 `web/src/api/types.generated.ts`（sync-check 类型同步）。
- `.gitignore` 增加本机工作区记忆目录 `.workbuddy/`。
