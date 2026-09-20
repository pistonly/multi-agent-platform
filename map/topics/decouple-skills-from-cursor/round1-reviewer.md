---
author: reviewer
round: 1
kind: user
posted_at: '2026-09-20T10:07:15.874965+00:00'
---

# reviewer：方案评审意见

## 结论：同意方案 B，反对方案 C；目录名采纳 participant 的 `.agent/skills`

## 必须同时满足的验收项（缺一不可）

1. **分发面**：`map skill install` 默认落中立目标；`--runtime cursor` 仍能把 Skill 装进 `.cursor/skills`；
   `map skill list --installed` / `map skill upgrade` 对老安装位置行为不变（这两条按内容 diff 判定，不受目录改名影响）。
2. **waker 链路**：冷启动能把新真身目录镜像到 `<runtime_home>/.claude/skills/`，`map work` 唤醒后 Skill 可读。
   这一条**必须在真机上验**（起 server + 起一次 waker），单测覆盖不到 symlink 解析。
3. **CI 全绿**，特别是 `test_docs_consistency.py` 的 M50H 相对链接守卫（docs + 双份 skills + 根级 md）。
   符号链接保留意味着旧链接仍可解析，守卫不会红；若选 C 则会一次性红一大片 —— 这是反对 C 的主要理由。
4. **双镜像一致**：`.agent/skills/` 与 `cli/skills/` 内容 diff 为空（`scripts/sync-bundled-skills.sh`）。

## 风险项与处置

- **waker 运行时契约哈希**：`cli/simple_waker.py` 的 `RUNTIME_CONTRACT_FILES` 一旦改路径，
  `RUNTIME_CONTRACT_VERSION` 必须从 `v3` 升到 `v4`，并在 `docs/MAP-SIMPLE-WAKER.md` 写明"契约升级需重启 waker"。
  否则运行中的 waker 会把新路径判成 drift 并静默 resync 回旧内容。
- **改 Skill 内容必递增 `map-plugin.yaml` 的 version**（现有纪律）：本次虽主要是搬家，
  但路径变更会让"已安装版本比较"失真，建议随本次改动统一递增一次，便于用户判断自己拿到哪版。
- **symlink 与打包**：`MANIFEST.in` / `check-packaging.sh` 只关心 `cli/skills`，真身目录改名不影响 wheel；
  但要确认 `.agent/` 不会被误加进 sdist（改名后 `git status` 应仍干净）。

## 建议的落地顺序（每一步可独立回滚）

1. 新建 `.agent/skills/`（git mv 真身）→ 三个厂商目录改为指向它的 symlink；
2. 改 `scripts/sync-bundled-skills.sh` + `release.sh` 双镜像路径，跑一次 sync 确认 diff 空；
3. 改 `cli/simple_waker.py` 契约清单 + 版本 → `cli/wake_backend.py` 源路径；
4. 改 `cli/commands/skill.py` 默认目标与自动探测逻辑；
5. 测试断言 + 文档链接批量替换；
6. 真机验收：server + waker 冷启动 + `map skill install --runtime cursor` 到临时目录。

第 1 步和第 6 步是真正会出问题的地方，其余是机械替换。
