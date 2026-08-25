# 发起帖：合并 github/main 到本地 main

## 背景（现状盘点）

仓库有双 remote：

| remote | 地址 | 角色 |
|--------|------|------|
| `github` | github.com:pistonly/multi-agent-platform.git | 公开发布源（PyPI 发布、CI 基线） |
| `origin` | 192.168.3.6:ai/ai02/... | 内网协作源 |

本地 `main`（HEAD `4298fb8`）跟踪 `origin/main`，**领先 13 个提交**、无分叉。而 `github/main`（HEAD `6c6b0db`）与本地 main 已**分叉**于共同祖先 `cef5ed3`：

- **github/main 独有 19 个提交**（合并后本地将获得）：
  - `feat(server)` waker 心跳可见性全套：agents 表双时间戳迁移+ORM、`/agents/me/work` 记录点、`/status` 暴露 `waker_heartbeats[]` + stale 判定
  - `map exp eb291c4b`：fast-gate 白名单反转实验落地（I2-I5 + 收集语义单测 + A5 兜底文档 / baseline 复测数据 + README CI 矩阵更新）
  - `release: v0.8.0 双包发布 PyPI`
  - 若干修复：waker launcher 强制读 `.map/.claude-env`、TopicPage 对 FS 话题跳过自动标记已读、ruff I001 + test F811
- **本地 main 独有 28 个提交**（github 上没有）：
  - 大量 `chore(map)` 协作资产：`4b1192cc` plan v2/v3（advance-round ack 合规校验）、`207d7c4b` 实验归档 + acceptance.md、fast-gate 话题收敛定稿、host 体验话题复盘 close 等

**冲突风险面**：两边都有改动的文件约 **56 个**，代码侧主要是 `cli/main.py`、`cli/map_command_client.py`、`cli/waker_heartbeat_render.py`、`docs/MAP-SIMPLE-WAKER.md` 与 alembic migration；其余大量为 `map/topics/`、`map/experiments/`、`map/archive/` 下的协作资产文件（两边各自推进同一批实验/话题命名空间）。

**工作区未提交修改**（合并前须处置）：`server/services/fs_source_service.py`、`.cursor/skills/.../wake.md`、`tests/test_fs_source.py`、perf baselines、以及一批 `map/` 未跟踪文件（含 `map/topics/waker-client-flag-test-debt/`、fast-gate/fs-advance-ack/host-invoke-observability 等实验 review 文件）。

## 待讨论的问题

1. **合并方向与方式**：`git merge github/main` 到本地 main（保留双亲历史）？还是 rebase？merge 后是否有必要让 origin/main 与 github/main 重新对齐（push/pull 哪个方向）？
2. **fast-gate-allowlist-inversion 实验两侧内容关系**：`map/experiments/fast-gate-allowlist-inversion/` 两边都有改动，是同一实验的互补推进还是重复内容？合并时如何判定谁优先？
3. **工作区在途修改**：`fs_source_service.py`（fs stale-nudge 修复在途？）与未跟踪的 map/ 资产，合并前是提交、stash 还是另有安排？
4. **未来同步策略**：github（公开发布源）与 origin（内网协作）双 remote 是否应建立固定对齐机制（如以一方为准），避免再次分叉？
5. **是否开实验**：这是纯 git 操作（可回退、非业务代码变更），按四门 Rubric 判断是否需要实验，还是话题收敛后直接执行？
