---
experiment: df22697d-d2b6-4572-a771-9529fa6cbca3
title: "M51 话题事实源收敛（v0.11）"
mode: standard
completed_at: 2026-08-15
---

# M51 执行日志

## 结果

五个子项全部完成：`map topic` 统一路由（slug / FS uuid5 / DB uuid）、FS 本地反查、文档收敛、DB → FS 单向迁移、ARCHITECTURE v2 重写。「先判别话题类型」与 uuid5 红线从 Agent 心智模型中移除，`map topic` 成为话题唯一推荐入口。

## 子项明细

### M51A 自动路由 ✅

- `cli/commands/topic.py` 的 `show / comment / advance-round / close` 的 `--id` 从 `uuid.UUID` 放宽为 `str`，新增 `_resolve_topic_ref()` 返回 `('db', uuid)` 或 `('fs', slug)`：
  - **uuid 格式** → DB API 优先；404 后 CLI 本地反查 FS uuid5（零 API）
  - **slug** → FS 优先（`map/topics/<slug>/` 存在即 FS）；未命中按 DB slug 匹配
  - **`--storage fs|db`** 显式覆盖，解决 DB slug 与 FS 同名话题冲突
- uuid 路径行为逐字节不变（先 DB 后反查），CLI 快照测试回归兜底。

### M51B FS 反查 ✅

- 路由层本地反查：`map_fs` uuid5 确定性派生 + workspace `map/topics/*/` 遍历比对。
- FS 命中后委派 `fs.py` 现有渲染（show）或 `fs advance-round/close` 验证型写 API 路径；comment = 纯本地写 round 文件（无 API 审计，与 `map fs comment` 语义一致）。
- 重复写同一 round 文件捕获 `FileExistsError`，提示 `map fs comment --force` 覆盖。
- 方案裁剪说明（已在 plan 记录）：不新增服务端注册表——`GET /projects/{id}/fs/topics` 投影每次实时扫描且响应已含 uuid5，CLI 本地反查零同步漂移风险。

### M51C 文档收敛 ✅

- `wake.md`：删「先判别话题类型」步骤与 uuid5 红线；`pending_topic_replies` 分发表双行合单行（统一 `map topic comment`）。
- `commands.md`：DB/FS 双列合并为单列 + `--storage` 说明；`map fs` 标注 advanced（离线/纯本地写场景）。
- 同步更新 `.cursor/skills/` 与 `cli/skills/` 双镜像，以及 topic-host / topic-participant checklist 引用。

### M51D 迁移命令 ✅

- `map topic migrate --id <uuid> --slug <name>`（登记写命令，dry-run 支持）：
  - DB show（含评论树）→ 写 FS `index.md`（frontmatter: title/status/round/participants）+ round 分组评论文件
  - 轮次划分：round summary 评论界定轮次；同人同轮多条评论合并为一个文件
  - 原子性：FS 完整落盘（index + 全部 round 文件）后才调 archive API，中途失败不产生半迁移
  - DB 记录 `archived_at`（列表默认隐藏，show 仍可见）

### M51E ARCHITECTURE v2 ✅

- `docs/ARCHITECTURE.md` 重写（588 行精简）：三层架构图（Agent 层 / 服务层 / 内容层）、三层分工表、实体全景（DB 实体 vs FS 派生实体）、FS 事实源约定表、M51 路由规则。

## 验收证据

| 验收项 | 证据 |
|--------|------|
| routing / migrate 新增测试全绿 | `pytest tests/test_topic_routing.py tests/test_topic_migrate.py tests/test_fs_source.py` → **43 passed** |
| fs 命令回归 | `pytest tests/cli/test_fs_persona.py tests/cli/test_compat.py` → **16 passed** |
| 快速门控全量回归 | `pytest tests/` → **395 passed, 2 skipped** |
| slug 路由实测 | `map topic show --id fs-refactor-review` → 解析到 FS 话题 `1ba6ab09` ✅ |
| FS uuid 路由实测 | `map topic show --id 1ba6ab09-...` → 同一话题 ✅ |
| DB uuid 路由实测 | `map topic show --id 19e0d9cc-...` → DB 话题「测试 host 编排模式」，行为不变 ✅ |
| migrate 实测 | `map topic migrate --id 19e0d9cc --slug host-orchestration-test --dry-run` → 输出 index.md + round1 文件 + archive 计划 ✅ |

## 遗留

- 无阻塞遗留。`map fs` 命令族保留为 advanced 入口（离线/纯本地写），不删除。
