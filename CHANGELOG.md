# Changelog

本文件记录面向使用者的变更（PyPI 包 `multi-agent-platform` / `multi-agent-platform-server`）。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
更早的历史见 git tag 与提交记录。

## [Unreleased]

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
