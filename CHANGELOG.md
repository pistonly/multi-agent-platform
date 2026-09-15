# Changelog

本文件记录面向使用者的变更（PyPI 包 `multi-agent-platform` / `multi-agent-platform-server`）。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
更早的历史见 git tag 与提交记录。

## [Unreleased]

### Changed

- **`map-project-collab` 的「工作方式协商」上移到「意图路由」之前**。原顺序下 Agent 按意图路由表
  走到 persona Skill 就离开了，读不到「先与用户确认协作拓扑 A/B/C/D」这道关卡（按错误假设行动
  会空转烧 token 或死等）。同时在入口分工表与意图路由表补了指向该节的钩子行，并补上原先只有
  方式 A 有条目、B/C/D 为空的路由项（方式 C → `map host invoke`；方式 D → 回报即结束，不轮询
  `map work`）。方式 B 由「三开对话框」改为「多开对话框」（常见 2–3 个，几个不限）。
  **Skill frontmatter description 未改动**——它每次都随 skill 列表进 prompt，属于常驻成本。

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
