---
title: "M51 话题事实源收敛（v0.11）"
acceptance:
  - "map topic show/comment --id <fs-slug> 与 --id <fs-uuid> 均成功（uuid5 解析，CLI 路由层完成）"
  - "map topic advance-round/close 同样支持 slug / fs-uuid 路由（uuid 仍走 DB，行为不变）"
  - "wake.md 不再包含「先判别话题类型」步骤与 uuid5 红线；pending_topic_replies 分发表合并为单列"
  - "commands.md DB/FS 双列合并为单列 + --storage 说明；map fs 标注 advanced（离线/纯本地写场景）"
  - "map topic migrate --id <uuid> --slug <name> 单向迁移 DB → FS，DB 记录 archived（列表默认隐藏，show 仍可见）"
  - "存量 DB 话题操作行为不变；map fs 全部命令回归通过"
  - "ARCHITECTURE.md 重写为 v2：三层分工、实体全景、FS 事实源、通知分层"
  - "新增路由/迁移能力有 pytest 覆盖，现有 topic/fs/文档护栏测试不回归"
evidence_keys:
  - "pytest tests/test_topic_routing.py（新）全绿"
  - "pytest tests/test_topic_migrate.py（新）全绿"
  - "pytest 快速门控全量 + tests/test_fs_commands.py 回归通过"
  - "map topic show --id <现有 fs slug> 实测成功；map topic show --id <db uuid> 实测行为不变"
dependencies:
  - "M50D/M50E 文档先统一（已完成）"
---

# M51 话题事实源收敛

## 目标

按 docs/prd/v0.11.md §5（M51）让 `map topic` 成为话题唯一推荐入口：CLI 内建 slug/uuid 自动路由，消灭「先判别话题类型」与 uuid5 红线；提供 DB → FS 单向迁移；重写 ARCHITECTURE.md v2。

## 方案裁剪说明（重要）

PRD 5.2.2 原文「FS 话题 uuid5 注册进服务端话题索引（bootstrap 与验证型写时同步）」。经核查服务端现状：`GET /projects/{id}/fs/topics` 投影端点**每次请求实时扫描文件系统**（server/api/fs.py，不经过内容 DB），响应即含每个 FS 话题的 uuid5 id。因此新增注册表 + 双时机同步属于重复建设；本实验将 uuid5→slug 解析放在 **CLI 路由层本地反查**（map_fs 确定性派生 + workspace 文件夹遍历，零 API），验收「--id <fs-uuid> 可用」等效满足且无同步漂移风险。若评审认为仍需服务端注册表，可在此基线上追加。

## 改动范围

| 子项 | 内容 |
|------|------|
| M51A 自动路由 | `cli/commands/topic.py`：show/comment/advance-round/close 的 `--id` 从 `uuid.UUID` 放宽为 `str`；新增 `_resolve_topic_ref()`：uuid 格式 → DB API（404 时本地反查 FS uuid5）；slug → FS；DB slug 与 FS 同名冲突时要求 `--storage fs\|db` |
| M51B FS 反查 | 路由层 helper：`map_fs.topic_id_from_slug(slug)` 对 workspace `map/topics/*/` 派生比对；FS 命中后委派 `fs.py` 现有渲染（show）或 `fs advance-round/close` API 路径（comment = 纯本地写 round 文件） |
| M51C 文档收敛 | wake.md 删「先判别」步骤与 uuid5 红线、分发表 pending_topic_replies 双行合单行；commands.md 双列合单列 + --storage + map fs advanced 标注；AGENTS.md 若有引用同步 |
| M51D 迁移命令 | `map topic migrate --id <uuid> --slug <name>`：DB show（含 comments）→ 写 FS index.md + round 分组评论文件 → DB `topic archive`（复用 archived_at 语义，reason=migrated、note 含 slug）；列表默认隐藏已满足 |
| M51E ARCHITECTURE v2 | docs/ARCHITECTURE.md 重写：CLI+Skill / 服务端 / FS 三层分工图、迁移后实体全景、FS 事实源约定、通知分层（轮询 / waker / SSE 规划） |

## 实现顺序

1. M51A+M51B（路由核心）→ tests/test_topic_routing.py
2. M51D（迁移）→ tests/test_topic_migrate.py
3. M51C（wake.md / commands.md 收敛）+ 文档护栏扩展
4. M51E（ARCHITECTURE.md v2）
5. 全量回归 + 实机验证

## 风险与对策

- `--id` 类型放宽影响现有调用：uuid 路径行为保持逐字节不变（先 DB API 后本地反查），CLI 快照测试回归兜底
- comment 路由到 FS 是纯本地写（无 API 审计）：与现有 `map fs comment` 语义一致，wake.md 已有说明；DB 话题 comment 行为不变
- migrate 写 FS 失败中途：先写 FS 完整落盘（index + 全部 round 文件）后才调 archive API，失败即中止不产生半迁移
