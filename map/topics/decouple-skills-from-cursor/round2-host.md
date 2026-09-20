---
author: host
round: 2
kind: user
posted_at: '2026-09-20T10:07:47.624529+00:00'
---

# host：收敛结论

## 决议

**采纳方案 B**，具体：

1. **真身迁到 `.agent/skills/`**（git mv，保留历史），`.cursor/skills` / `.claude/skills` / `.codex/skills`
   三个目录全部改为指向 `../.agent/skills/` 的符号链接 —— 既去掉"真身叫 .cursor"的绑定，
   又保住各 runtime 的自动发现能力和历史文档链接。
2. **`map skill install` 默认改为中立目标 + 自动探测**：已存在 `.cursor/` → 装 `.cursor/skills`；
   已存在 `.claude/` / `.codex/` → 对应目录；都没有 → `.agent/skills` 并提示 `--runtime` 可指定。
   `--target` / `--runtime` 显式指定时行为不变。
3. **waker 运行时契约版本化**：`RUNTIME_CONTRACT_VERSION` v3 → v4，契约清单路径同步改为 `.agent/skills/**`，
   `docs/MAP-SIMPLE-WAKER.md` 写明"契约升级需重启 waker"，避免运行中 waker 把新路径判成 drift 后静默回写旧内容。
4. **双镜像与版本纪律不变**：改完必跑 `scripts/sync-bundled-skills.sh`，
   并统一递增一次各 Skill 的 `map-plugin.yaml` version。

## 落地顺序（每步可独立回滚）

1. git mv 真身 → 重建三个 symlink；
2. 改 `sync-bundled-skills.sh` + `release.sh` 双镜像路径，sync 后确认 diff 空；
3. 改 `cli/simple_waker.py` 契约清单 + 版本、`cli/wake_backend.py` 源路径；
4. 改 `cli/commands/skill.py` 默认目标与探测逻辑；
5. 测试断言 + 文档链接批量替换（`test_docs_consistency.py` M50H 守卫），symlink 保留使旧链接仍可解析；
6. **真机验收**：起 server + waker 冷启动，确认 Skill 能镜像到 runtime home；
   再 `map skill install --runtime cursor` 到临时目录验证老路径仍可用。

## 未决（不在本话题闭环内）

- 实施主体：本轮讨论只定方案。是否由被唤醒的 Agent Runtime（waker + claude/cursor runtime）自动执行改造，
  还是人工按上面 6 步执行 —— 需项目负责人确认后另立实验跟踪。自动执行的代价是"外部 agent 直接改仓库 +
  触碰发版纪律（双镜像 / 版本递增 / CHANGELOG）"，需要显式授权。

## 遗留不动

- `.map/.cursor-env` 与 Cursor runtime 支持本身保留：那是 waker 的可选 runtime 后端，不是目录耦合。
